from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .contracts import ActorType, EvalCase, RawEvent
from .hashing import sha256_text
from .privacy import PrivateCorpusMapping
from .storage import Store


TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".toml"}


def deterministic_file_event(
    path: str | Path,
    *,
    root: str | Path | None = None,
    private: bool = False,
) -> RawEvent:
    source = Path(path).resolve()
    raw_content = source.read_text(encoding="utf-8")
    locator = _locator(source, root)
    content_hash = sha256_text(raw_content)
    event_id = f"event:file:{sha256_text(locator + ":" + content_hash)[:20]}"
    timestamp = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
    return RawEvent(
        id=event_id,
        timestamp=timestamp,
        actor_id=(PrivateCorpusMapping(source.parent).actor_id(source) if private else f"document:{source.name}"),
        actor_type=ActorType.DOCUMENT,
        source_type="file",
        source_locator=(PrivateCorpusMapping(source.parent).locator(source) if private else locator),
        raw_content=raw_content,
        metadata={
            "ingest": "file",
            "content_encoding": "utf-8",
            "raw_content_hash": content_hash,
            "privacy": "private" if private else "public-source",
        },
    )


def ingest_path(store: Store, path: str | Path, *, private: bool = False) -> list[str]:
    target = Path(path).resolve()
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(
            candidate
            for candidate in target.rglob("*")
            if candidate.is_file()
            and candidate.suffix.lower() in TEXT_SUFFIXES
            and ".team-soul" not in candidate.parts
            and ".git" not in candidate.parts
        )
    else:
        raise FileNotFoundError(target)
    root = target if target.is_dir() else target.parent
    events = [deterministic_file_event(file, root=root, private=private) for file in files]
    store.append_events(events)
    return [event.id for event in events]


def fixture_event(event: dict) -> RawEvent:
    return RawEvent.model_validate(event)


def load_fixture(path: str | Path, *, ignore_keys: Sequence[str] = ()) -> EvalCase:
    """Load a fixture, optionally ignoring caller-declared envelope keys.

    EvalCase forbids extra fields, so an outer envelope (for example the E2
    evidence_level/human_approved/provenance/permission_boundary block) must be
    stripped before schema validation rather than declared on the case contract.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ignore_keys:
        payload.pop(key, None)
    events = [fixture_event(item) for item in payload.pop("events", [])]
    payload["events"] = events
    return EvalCase.model_validate(payload)


def load_fixtures(directory: str | Path) -> list[EvalCase]:
    fixture_dir = Path(directory)
    return [load_fixture(path) for path in sorted(fixture_dir.glob("C*.json"))]


def _locator(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return str(path)


def append_json_event(store: Store, payload: str) -> str:
    event = RawEvent.model_validate(json.loads(payload))
    store.append_event(event)
    return event.id


def make_correction_event(
    original: RawEvent,
    *,
    event_id: str,
    corrected_content: str,
    timestamp: datetime | None = None,
    actor_id: str | None = None,
    actor_type: ActorType = ActorType.HUMAN,
    source_type: str = "correction",
    source_locator: str | None = None,
    reason: str = "",
) -> RawEvent:
    """Create a new event that corrects an existing event without rewriting it."""
    metadata = dict(original.metadata)
    metadata.update(
        {
            "event_kind": "correction",
            "correction_of": original.id,
            "correction_reason": reason,
        }
    )
    return RawEvent(
        id=event_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        actor_id=actor_id or original.actor_id,
        actor_type=actor_type,
        source_type=source_type,
        source_locator=source_locator or f"correction:{original.id}",
        raw_content=corrected_content,
        project_hints=list(original.project_hints),
        problem_hints=list(original.problem_hints),
        metadata=metadata,
    )
