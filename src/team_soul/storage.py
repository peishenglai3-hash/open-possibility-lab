from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from .contracts import (
    Claim,
    EvidenceRef,
    OperationalClosure,
    ProblemThread,
    RawEvent,
    Relation,
    ReopenSignal,
)
from .hashing import canonical_json, sha256_json


def _json(value: Any) -> str:
    return canonical_json(value)


def _load(value: str) -> Any:
    return json.loads(value)


def _dt(value: datetime) -> str:
    return value.isoformat()


class CorruptRawEvent(RuntimeError):
    """G0-F: one unusable raw row is quarantined instead of bricking the whole store."""

    def __init__(self, event_id: str, detail: str) -> None:
        super().__init__(f"raw event {event_id!r} is corrupt: {detail}")
        self.event_id = event_id


def _row_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Store:
    """SQLite source/projection store.

    Raw events are append-only. Everything under the projection tables is
    rebuildable from raw events and therefore never becomes a second source of
    truth.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "Store":
        self.init_schema()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_raw_events(self) -> None:
        """Additive, idempotent G0-A migration for the optional dual-time columns.

        Existing stores are upgraded in place; no event is rewritten and no hash changes.
        """

        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(raw_events)")
        }
        additions = (
            ("occurred_at", "TEXT"),
            ("known_at", "TEXT"),
            ("target_at", "TEXT"),
            ("knowledge_basis", "TEXT NOT NULL DEFAULT ''"),
        )
        changed = False
        for name, ddl in additions:
            if name not in columns:
                self.connection.execute(f"ALTER TABLE raw_events ADD COLUMN {name} {ddl}")
                changed = True
        if changed:
            self.connection.commit()

    def init_schema(self) -> None:
        """Create the schema, then apply additive idempotent migrations."""

        self._init_schema_impl()
        self._migrate_raw_events()

    def _init_schema_impl(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_events (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                project_hints_json TEXT NOT NULL,
                problem_hints_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                occurred_at TEXT,
                known_at TEXT,
                target_at TEXT,
                knowledge_basis TEXT NOT NULL DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS raw_events_fts USING fts5(
                event_id UNINDEXED,
                raw_content,
                source_type,
                actor_id,
                tokenize = 'unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS raw_events_append_only_update
            BEFORE UPDATE ON raw_events
            BEGIN
                SELECT RAISE(ABORT, 'raw_events is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS raw_events_append_only_delete
            BEFORE DELETE ON raw_events
            BEGIN
                SELECT RAISE(ABORT, 'raw_events is append-only');
            END;

            CREATE TABLE IF NOT EXISTS problems (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS problem_event_links (
                problem_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                PRIMARY KEY (problem_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_refs (
                id TEXT PRIMARY KEY,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relations (
                src_id TEXT NOT NULL,
                dst_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (src_id, dst_id, relation_type)
            );
            CREATE TABLE IF NOT EXISTS closures (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS closure_links (
                closure_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                link_kind TEXT NOT NULL,
                PRIMARY KEY (closure_id, event_id, link_kind)
            );
            CREATE TABLE IF NOT EXISTS reopen_signals (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_logs (
                run_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_registry (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_hash TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS policy_registry_append_only_update
            BEFORE UPDATE ON policy_registry
            BEGIN
                SELECT RAISE(ABORT, 'policy_registry is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS policy_registry_append_only_delete
            BEFORE DELETE ON policy_registry
            BEGIN
                SELECT RAISE(ABORT, 'policy_registry is append-only');
            END;

            CREATE TABLE IF NOT EXISTS effect_ledger (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_hash TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS effect_ledger_append_only_update
            BEFORE UPDATE ON effect_ledger
            BEGIN
                SELECT RAISE(ABORT, 'effect_ledger is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS effect_ledger_append_only_delete
            BEFORE DELETE ON effect_ledger
            BEGIN
                SELECT RAISE(ABORT, 'effect_ledger is append-only');
            END;

            CREATE TABLE IF NOT EXISTS pilot_feedback (
                feedback_id TEXT PRIMARY KEY,
                reviewed_at TEXT NOT NULL,
                case_id TEXT NOT NULL,
                suggestion_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                rating TEXT NOT NULL,
                live_use_confirmed INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS pilot_feedback_append_only_update
            BEFORE UPDATE ON pilot_feedback
            BEGIN
                SELECT RAISE(ABORT, 'pilot_feedback is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS pilot_feedback_append_only_delete
            BEFORE DELETE ON pilot_feedback
            BEGIN
                SELECT RAISE(ABORT, 'pilot_feedback is append-only');
            END;

            CREATE TABLE IF NOT EXISTS experiment_ledger (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS experiment_ledger_append_only_update
            BEFORE UPDATE ON experiment_ledger
            BEGIN
                SELECT RAISE(ABORT, 'experiment_ledger is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS experiment_ledger_append_only_delete
            BEFORE DELETE ON experiment_ledger
            BEGIN
                SELECT RAISE(ABORT, 'experiment_ledger is append-only');
            END;
            """
        )
        self.connection.commit()

    def append_experiment_run(self, payload: dict[str, Any]) -> str:
        """Persist one immutable experiment receipt outside raw evidence."""

        self.init_schema()
        payload_json = _json(payload)
        content_hash = sha256_json(payload)
        run_id = str(payload["run_id"])
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM experiment_ledger WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise ValueError(f"experiment run id collision: {run_id}")
                return run_id
            connection.execute(
                """
                INSERT INTO experiment_ledger(
                    run_id, started_at, finished_at, payload_json, content_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(payload["started_at"]),
                    str(payload["finished_at"]),
                    payload_json,
                    content_hash,
                ),
            )
        return run_id

    def list_experiment_runs(self) -> list[dict[str, Any]]:
        self.init_schema()
        rows = self.connection.execute(
            "SELECT payload_json FROM experiment_ledger ORDER BY started_at, run_id"
        ).fetchall()
        return [_load(row["payload_json"]) for row in rows]

    def append_pilot_feedback(self, payload: dict[str, Any]) -> str:
        """Persist human pilot review as append-only evaluation evidence."""

        self.init_schema()
        payload_json = _json(payload)
        content_hash = sha256_json(payload)
        feedback_id = f"pilot-feedback:{content_hash[:24]}"
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM pilot_feedback WHERE feedback_id = ?",
                (feedback_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise ValueError(f"pilot feedback id collision: {feedback_id}")
                return feedback_id
            connection.execute(
                """
                INSERT INTO pilot_feedback(
                    feedback_id, reviewed_at, case_id, suggestion_id, actor_id,
                    rating, live_use_confirmed, payload_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    str(payload["reviewed_at"]),
                    str(payload["case_id"]),
                    str(payload["suggestion_id"]),
                    str(payload["actor_id"]),
                    str(payload["rating"]),
                    int(bool(payload.get("live_use_confirmed", False))),
                    payload_json,
                    content_hash,
                ),
            )
        return feedback_id

    def list_pilot_feedback(self) -> list[dict[str, Any]]:
        self.init_schema()
        rows = self.connection.execute(
            "SELECT payload_json FROM pilot_feedback ORDER BY reviewed_at, feedback_id"
        ).fetchall()
        return [_load(row["payload_json"]) for row in rows]

    def append_policy_event(
        self,
        payload: dict[str, Any],
        *,
        timestamp: datetime,
        policy_id: str,
        operation: str,
        actor_id: str,
        actor_type: str,
    ) -> str:
        """Append policy control metadata without expanding raw event history."""

        self.init_schema()
        payload_json = _json(payload)
        event_hash = sha256_json(payload)
        event_id = f"policy:{event_hash[:24]}"
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT event_hash FROM policy_registry WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["event_hash"] != event_hash:
                    raise ValueError(f"policy event id collision: {event_id}")
                return event_id
            connection.execute(
                """
                INSERT INTO policy_registry(
                    event_id, timestamp, policy_id, operation, actor_id,
                    actor_type, payload_json, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _dt(timestamp),
                    policy_id,
                    operation,
                    actor_id,
                    actor_type,
                    payload_json,
                    event_hash,
                ),
            )
        return event_id

    def list_policy_events(self) -> list[tuple[datetime, dict[str, Any]]]:
        self.init_schema()
        rows = self.connection.execute(
            """
            SELECT timestamp, payload_json
            FROM policy_registry
            ORDER BY timestamp, event_id
            """
        ).fetchall()
        return [(_row_datetime(row["timestamp"]), _load(row["payload_json"])) for row in rows]

    def append_effect_event(
        self,
        payload: dict[str, Any],
        *,
        timestamp: datetime,
        effect_id: str,
        operation: str,
        actor_id: str,
        actor_type: str,
    ) -> str:
        """Append effect audit metadata without mutating raw event history."""

        self.init_schema()
        payload_json = _json(payload)
        event_hash = sha256_json(payload)
        event_id = f"effect:{event_hash[:24]}"
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT event_hash FROM effect_ledger WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["event_hash"] != event_hash:
                    raise ValueError(f"effect event id collision: {event_id}")
                return event_id
            connection.execute(
                """
                INSERT INTO effect_ledger(
                    event_id, timestamp, effect_id, operation, actor_id,
                    actor_type, payload_json, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _dt(timestamp),
                    effect_id,
                    operation,
                    actor_id,
                    actor_type,
                    payload_json,
                    event_hash,
                ),
            )
        return event_id

    def list_effect_events(self) -> list[tuple[datetime, dict[str, Any]]]:
        self.init_schema()
        rows = self.connection.execute(
            """
            SELECT timestamp, payload_json
            FROM effect_ledger
            ORDER BY timestamp, event_id
            """
        ).fetchall()
        return [(_row_datetime(row["timestamp"]), _load(row["payload_json"])) for row in rows]

    def append_event(self, event: RawEvent) -> None:
        self.init_schema()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO raw_events (
                    id, timestamp, actor_id, actor_type, source_type,
                    source_locator, raw_content, project_hints_json,
                    problem_hints_json, metadata_json, content_hash,
                    occurred_at, known_at, target_at, knowledge_basis
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    _dt(event.timestamp),
                    event.actor_id,
                    str(event.actor_type),
                    event.source_type,
                    event.source_locator,
                    event.raw_content,
                    _json(event.project_hints),
                    _json(event.problem_hints),
                    _json(event.metadata),
                    event.content_hash,
                    _dt(event.occurred_at) if event.occurred_at is not None else None,
                    _dt(event.known_at) if event.known_at is not None else None,
                    _dt(event.target_at) if event.target_at is not None else None,
                    event.knowledge_basis,
                ),
            )
            connection.execute(
                "INSERT INTO raw_events_fts(event_id, raw_content, source_type, actor_id) VALUES (?, ?, ?, ?)",
                (event.id, event.raw_content, event.source_type, event.actor_id),
            )

    def append_correction(self, original_event_id: str, correction: RawEvent) -> None:
        """Append a correction event; the original raw event remains untouched."""
        original = self.get_event(original_event_id)
        if original is None:
            raise KeyError(f"original raw event not found: {original_event_id}")
        if correction.id == original_event_id:
            raise ValueError("correction must have a new event id")
        metadata = dict(correction.metadata)
        metadata.setdefault("event_kind", "correction")
        metadata["correction_of"] = original_event_id
        # FR-12: the canonical envelope hash must be recomputed after the correction
        # metadata is attached, otherwise re-validation rejects a normally constructed
        # correction event.
        payload = correction.model_dump(mode="json")
        payload.pop("content_hash", None)
        payload["metadata"] = metadata
        corrected = RawEvent.model_validate(payload)
        self.append_event(corrected)

    def append_events(self, events: Sequence[RawEvent]) -> int:
        count = 0
        for event in events:
            try:
                self.append_event(event)
            except sqlite3.IntegrityError as exc:
                if "raw_events.id" not in str(exc):
                    raise
                existing = self.get_event(event.id)
                if existing is None or existing.content_hash != event.content_hash:
                    raise ValueError(
                        f"raw event id collision with different content: {event.id}"
                    ) from exc
                continue
            count += 1
        return count

    def get_event(self, event_id: str) -> RawEvent | None:
        self.init_schema()
        row = self.connection.execute(
            "SELECT * FROM raw_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._event_from_row(row) if row else None

    def list_events(self, limit: int | None = None) -> list[RawEvent]:
        self.init_schema()
        query = "SELECT * FROM raw_events ORDER BY timestamp, id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.connection.execute(query, params).fetchall()
        events: list[RawEvent] = []
        for row in rows:
            try:
                events.append(self._event_from_row(row))
            except CorruptRawEvent:
                # G0-F: a single tampered row no longer makes every read fail.
                continue
        return events

    @property
    def corrupt_raw_events(self) -> list[dict[str, Any]]:
        """Quarantined rows (id + reason) reported by G0-F, never silently dropped."""

        return list(getattr(self, "_corrupt_raw_events", []))

    def _event_from_row(self, row: sqlite3.Row) -> RawEvent:
        try:
            return self._build_event(row)
        except CorruptRawEvent:
            raise
        except Exception as exc:
            record = {"id": str(row["id"]), "error": f"{type(exc).__name__}: {exc}"}
            self._corrupt_raw_events = [*self.corrupt_raw_events, record]
            raise CorruptRawEvent(str(row["id"]), str(exc)) from exc

    def _build_event(self, row: sqlite3.Row) -> RawEvent:
        return RawEvent(
            id=row["id"],
            timestamp=_row_datetime(row["timestamp"]),
            actor_id=row["actor_id"],
            actor_type=row["actor_type"],
            source_type=row["source_type"],
            source_locator=row["source_locator"],
            raw_content=row["raw_content"],
            project_hints=_load(row["project_hints_json"]),
            problem_hints=_load(row["problem_hints_json"]),
            metadata=_load(row["metadata_json"]),
            content_hash=row["content_hash"],
            # G0-A: the optional dual-time fields are persisted as columns, so a stored
            # event reconstructs to the same canonical envelope and content hash.
            occurred_at=_row_datetime(row["occurred_at"]) if row["occurred_at"] else None,
            known_at=_row_datetime(row["known_at"]) if row["known_at"] else None,
            target_at=_row_datetime(row["target_at"]) if row["target_at"] else None,
            knowledge_basis=row["knowledge_basis"] or "",
        )

    def search_events(
        self,
        query: str,
        limit: int = 30,
        *,
        as_of: datetime | None = None,
    ) -> list[tuple[RawEvent, float]]:
        """Search FTS first, then use a deterministic lexical fallback.

        The fallback is intentional: short Chinese queries and punctuation-heavy
        research notes do not always tokenize usefully under SQLite unicode61.
        """
        self.init_schema()
        terms = [term for term in query.replace("\n", " ").split() if term]
        match = " OR ".join(self._fts_quote(term) for term in terms[:12])
        rows: list[sqlite3.Row] = []
        if match:
            try:
                rows = self.connection.execute(
                    """
                    SELECT r.*, bm25(raw_events_fts) AS rank
                    FROM raw_events_fts f
                    JOIN raw_events r ON r.id = f.event_id
                    WHERE raw_events_fts MATCH ?
                      AND (? IS NULL OR r.timestamp <= ?)
                    ORDER BY rank, r.timestamp, r.id
                    LIMIT ?
                    """,
                    (match, _dt(as_of) if as_of is not None else None, _dt(as_of) if as_of is not None else None, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        results = [(self._event_from_row(row), 1.0 / (index + 1)) for index, row in enumerate(rows)]
        seen = {event.id for event, _ in results}
        if len(results) < limit:
            for event in self.list_events():
                if as_of is not None and event.timestamp > as_of:
                    continue
                if event.id in seen:
                    continue
                haystack = f"{event.raw_content} {event.source_type} {event.actor_id}".lower()
                score = self._lexical_score(query, haystack)
                if score > 0:
                    results.append((event, score))
            results.sort(key=lambda item: (-item[1], item[0].timestamp, item[0].id))
        return results[:limit]

    @staticmethod
    def _fts_quote(term: str) -> str:
        safe = term.replace('"', ' ')
        return f'"{safe}"'

    @staticmethod
    def _lexical_score(query: str, haystack: str) -> float:
        raw_terms = [term.strip(' ,.;:!?()[]{}"\'') for term in query.lower().split()]
        terms = [term for term in raw_terms if term]
        if not terms:
            return 0.0
        hits = sum(1 for term in terms if term in haystack)
        return hits / len(terms)

    def clear_projection(self) -> None:
        self.init_schema()
        with self.transaction() as connection:
            for table in (
                "problems",
                "problem_event_links",
                "claims",
                "evidence_refs",
                "relations",
                "closures",
                "closure_links",
                "reopen_signals",
            ):
                connection.execute(f"DELETE FROM {table}")

    def replace_projection(
        self,
        problems: Sequence[ProblemThread],
        claims: Sequence[Claim],
        evidence_refs: Sequence[tuple[str, str, str, EvidenceRef]],
        relations: Sequence[Relation],
        closures: Sequence[OperationalClosure],
        reopen_signals: Sequence[ReopenSignal] = (),
    ) -> str:
        self.clear_projection()
        with self.transaction() as connection:
            for problem in problems:
                connection.execute(
                    "INSERT INTO problems(id, payload_json) VALUES (?, ?)",
                    (problem.id, problem.model_dump_json()),
                )
                for event_id in problem.source_event_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO problem_event_links(problem_id, event_id) VALUES (?, ?)",
                        (problem.id, event_id),
                    )
            for claim in claims:
                connection.execute(
                    "INSERT INTO claims(id, payload_json) VALUES (?, ?)",
                    (claim.id, claim.model_dump_json()),
                )
            for evidence_id, owner_type, owner_id, evidence in evidence_refs:
                connection.execute(
                    "INSERT INTO evidence_refs(id, owner_type, owner_id, payload_json) VALUES (?, ?, ?, ?)",
                    (evidence_id, owner_type, owner_id, evidence.model_dump_json()),
                )
            for relation in relations:
                connection.execute(
                    """
                    INSERT INTO relations(src_id, dst_id, relation_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        relation.src_id,
                        relation.dst_id,
                        relation.relation_type,
                        relation.model_dump_json(),
                    ),
                )
            for closure in closures:
                connection.execute(
                    "INSERT INTO closures(id, payload_json) VALUES (?, ?)",
                    (closure.id, closure.model_dump_json()),
                )
                for evidence in (
                    closure.supporting_evidence,
                    closure.dissent_refs,
                    closure.unresolved_refs,
                    closure.validation_evidence,
                ):
                    kind = "supporting"
                    if evidence is closure.dissent_refs:
                        kind = "dissent"
                    elif evidence is closure.unresolved_refs:
                        kind = "unresolved"
                    elif evidence is closure.validation_evidence:
                        kind = "validation"
                    for item in evidence:
                        connection.execute(
                            "INSERT OR IGNORE INTO closure_links(closure_id, event_id, link_kind) VALUES (?, ?, ?)",
                            (closure.id, item.source_event_id, kind),
                        )
            for signal in reopen_signals:
                connection.execute(
                    "INSERT INTO reopen_signals(id, payload_json) VALUES (?, ?)",
                    (signal.id, signal.model_dump_json()),
                )
        projection = self.projection_payload()
        projection_hash = sha256_json(projection)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_meta(key, value) VALUES('projection_hash', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (projection_hash,),
            )
        return projection_hash

    def projection_payload(self) -> dict[str, Any]:
        self.init_schema()
        payload: dict[str, Any] = {}
        for table, key in (
            ("problems", "problems"),
            ("claims", "claims"),
            ("evidence_refs", "evidence_refs"),
            ("relations", "relations"),
            ("closures", "closures"),
            ("reopen_signals", "reopen_signals"),
        ):
            rows = self.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            payload[key] = [dict(row) for row in rows]
        payload["problem_event_links"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM problem_event_links ORDER BY problem_id, event_id"
            ).fetchall()
        ]
        payload["closure_links"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM closure_links ORDER BY closure_id, event_id, link_kind"
            ).fetchall()
        ]
        return payload

    def projection_hash(self) -> str | None:
        self.init_schema()
        row = self.connection.execute(
            "SELECT value FROM runtime_meta WHERE key = 'projection_hash'"
        ).fetchone()
        return row[0] if row else None

    def get_runtime_meta(self, key: str) -> str | None:
        self.init_schema()
        row = self.connection.execute(
            "SELECT value FROM runtime_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    def set_runtime_meta(self, key: str, value: str) -> None:
        self.init_schema()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def save_run_log(self, payload: dict[str, Any]) -> None:
        self.init_schema()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO run_logs(run_id, case_id, arm, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (payload["run_id"], payload["case_id"], payload["arm"], _json(payload)),
            )

    def list_projection(self, table: str, limit: int | None = None) -> list[dict[str, Any]]:
        self.init_schema()
        allowed = {"problems", "claims", "evidence_refs", "relations", "closures", "reopen_signals"}
        if table not in allowed:
            raise ValueError(f"Unsupported projection table: {table}")
        query = f"SELECT * FROM {table} ORDER BY rowid"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        return [dict(row) for row in self.connection.execute(query, params).fetchall()]
