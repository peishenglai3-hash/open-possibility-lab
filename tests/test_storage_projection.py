from pathlib import Path
import sqlite3

from team_soul.ingest import load_fixture
from team_soul.ingest import make_correction_event
from team_soul.projection import (
    load_claims,
    load_dissent_records,
    load_problems,
    rebuild_projection,
    reactivate_dissent,
)
from team_soul.storage import Store


def test_rebuild_is_stable_and_raw_history_survives(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C01_historical_revision.json"))
    store = Store(tmp_path / "team.sqlite3")
    store.init_schema()
    assert store.append_events(case.events) == len(case.events)
    first = rebuild_projection(store)
    second = rebuild_projection(store)
    assert first == second
    assert len(store.list_events()) == len(case.events)
    assert any(problem.id == "problem:c01_batch_size" for problem in load_problems(store))
    assert any(claim.id == "claim:c01_current" for claim in load_claims(store))
    store.close()


def test_duplicate_append_is_idempotent(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C03_dissent_preservation.json"))
    store = Store(tmp_path / "team.sqlite3")
    store.init_schema()
    assert store.append_events(case.events) == len(case.events)
    assert store.append_events(case.events) == 0
    assert len(store.list_events()) == len(case.events)
    store.close()


def test_dissent_history_is_rebuildable_and_reactivation_is_derived(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C03_dissent_preservation.json"))
    store = Store(tmp_path / "dissent.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    rebuild_projection(store)

    records = load_dissent_records(store)
    dissent = next(record for record in records if record.actor_id == "sara")
    assert dissent.status == "PRESERVED"
    assert dissent.relevance_condition
    reactivated = reactivate_dissent(dissent, outcome="new scoped review reopened the objection")
    assert reactivated.status == "REACTIVATED"
    assert dissent.status == "PRESERVED"
    assert reactivated.source_event_ids == dissent.source_event_ids
    store.close()


def test_correction_is_a_new_append_only_event(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C01_historical_revision.json"))
    store = Store(tmp_path / "correction.sqlite3")
    store.init_schema()
    store.append_event(case.events[0])
    correction = make_correction_event(
        case.events[0],
        event_id="evt:correction",
        corrected_content="Corrected historical observation.",
        reason="source transcription fix",
    )
    store.append_correction(case.events[0].id, correction)
    original = store.get_event(case.events[0].id)
    stored_correction = store.get_event("evt:correction")
    assert original is not None and original.raw_content == case.events[0].raw_content
    assert stored_correction is not None
    assert stored_correction.metadata["correction_of"] == case.events[0].id
    assert len(store.list_events()) == 2
    try:
        store.connection.execute("UPDATE raw_events SET raw_content = 'tampered' WHERE id = ?", (case.events[0].id,))
    except sqlite3.DatabaseError as exc:
        assert "append-only" in str(exc)
    else:
        raise AssertionError("raw history update was not blocked")
    store.close()
