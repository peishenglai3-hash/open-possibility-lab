"""FR-12 / FR-13 regressions.

FR-12 (Low, fail-closed): Storage.append_correction mutated metadata AFTER dumping the already
hashed event, so re-validation rejected a normally constructed correction event with
"content_hash does not match canonical RawEvent envelope". Only ingest.make_correction_event,
which pre-populates the metadata, worked.

FR-13 (Low): the documented CLI option "event cursor --as-of 2026-01-01" crashed with
TypeError: can not compare offset-naive and offset-aware datetimes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from team_soul.cli import event_cursor
from team_soul.config import load_config
from team_soul.contracts import ActorType, RawEvent
from team_soul.storage import Store

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _event(event_id: str, actor: str, content: str) -> RawEvent:
    return RawEvent(
        id=event_id,
        timestamp=BASE,
        actor_id=actor,
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator=f"test/{event_id}",
        raw_content=content,
    )


def test_append_correction_accepts_a_plain_correction_event(tmp_path: Path) -> None:
    store = Store(tmp_path / "correction.sqlite3")
    store.init_schema()
    original = _event("evt:orig", "alice", "original statement")
    store.append_event(original)
    before = store.get_event("evt:orig").content_hash

    correction = RawEvent(
        id="evt:corr",
        timestamp=BASE + timedelta(days=1),
        actor_id="bob",
        actor_type=ActorType.HUMAN,
        source_type="correction",
        source_locator="test/correction",
        raw_content="corrected statement",
    )
    store.append_correction("evt:orig", correction)

    stored = store.get_event("evt:corr")
    assert stored is not None
    assert stored.metadata["correction_of"] == "evt:orig"
    assert stored.metadata["event_kind"] == "correction"
    assert stored.verify_content_hash() is True
    assert store.get_event("evt:orig").content_hash == before
    store.close()


def test_event_cursor_accepts_a_date_only_as_of(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime"
    config_path = tmp_path / "team_soul.toml"
    config_path.write_text(
        "[runtime]\ndata_dir = \"" + data_dir.as_posix() + "\"\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    store = Store(config.sqlite_path)
    store.init_schema()
    store.append_event(_event("evt:cursor", "alice", "observed note"))
    store.close()

    event_cursor("alice", as_of="2026-01-01", config=config_path)
