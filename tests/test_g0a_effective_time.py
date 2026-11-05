"""G0-A failing-first suite: unified effective time (ADR docs/V03-GATE0-ADR-EFFECTIVE-TIME.md).

Tests T-A1..T-A7 are written BEFORE the implementation, per the V0.3 work order
("failing test -> minimal fix -> focused tests -> full regression"). Expected state on the
pre-implementation tree: T-A2 and T-A5 FAIL because RawEvent has no occurred_at/known_at and
RuntimeContext has no effective_time; T-A1 FAILS because check_reopen is not time-bounded
(FR-16). T-A3/T-A4/T-A6/T-A7 are consistency guards that must keep passing during and after
the change (T-A7 is the zero-drift guard for the frozen epoch-2 corpus).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from team_soul.config import RuntimeConfig
from team_soul.hashing import sha256_json
from team_soul.contracts import (
    ActorType,
    BoundaryAssessment,
    ClosureProposal,
    ClosureStatus,
    EvidenceKind,
    EvidenceRef,
    RawEvent,
)
from team_soul.ingest import load_fixture
from team_soul.kernel.contracts import RuntimeContext, RuntimeMode
from team_soul.operators import check_reopen, persist_closure_proposal, validate_closure
from team_soul.projection import rebuild_projection
from team_soul.retrieval import retrieve
from team_soul.scenario import build_scenario_matrix, scenario_manifest
from team_soul.storage import Store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "C01_historical_revision.json"
PROBLEM = "problem:c01_batch_size"
BASE = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
EPOCH2_MANIFEST = "ecebc805cb46bbdbc7745223b970f804e1466a65eaacba1bea5320a6e96ae00e"


def _active_closure(tmp_path: Path):
    case = load_fixture(FIXTURE)
    store = Store(tmp_path / "g0a.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    config = RuntimeConfig()
    persist_closure_proposal(case.closure_proposal, store, config)
    closure = validate_closure(case.closure_proposal, store, config)
    assert closure.status == ClosureStatus.ACTIVE
    return store, case, closure, config


def _future_event(event_id: str, *, metadata: dict | None = None, when: datetime | None = None) -> RawEvent:
    return RawEvent(
        id=event_id,
        timestamp=when or (BASE + timedelta(days=3650)),
        actor_id="future-bot",
        actor_type=ActorType.TOOL,
        source_type="future",
        source_locator=f"future/{event_id}",
        raw_content="batch size recommendation alpha pipeline",
        problem_hints=[PROBLEM],
        metadata=metadata or {},
    )


def test_ta1_future_event_cannot_reopen_a_closure(tmp_path: Path) -> None:
    """FR-16: a future-dated metadata trigger must not reopen an ACTIVE closure."""

    store, _case, closure, _config = _active_closure(tmp_path)
    future = _future_event("evt:future-reopen", metadata={"reopen_problem_ids": [PROBLEM]})
    store.append_event(future)

    decision_time = max(event.timestamp for event in _case.events)
    _reopened, signals = check_reopen(closure, [future], effective_time=decision_time)
    assert signals == [], "future-dated event reopened a closure (FR-16 not fixed)"


def test_ta2_not_yet_known_event_is_invisible_at_effective_time(tmp_path: Path) -> None:
    """A record that happened before T but was only knowable after T must be invisible at T."""

    store = Store(tmp_path / "g0a_known.sqlite3")
    store.init_schema()
    occurred = BASE
    known_later = occurred + timedelta(days=30)
    late = RawEvent(
        id="evt:late-knowledge",
        timestamp=occurred,
        occurred_at=occurred,
        known_at=known_later,
        knowledge_basis="trusted-ingest",
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="lab_note",
        source_locator="lab/alpha/late",
        raw_content="batch size recommendation alpha pipeline",
        problem_hints=[PROBLEM],
    )
    store.append_event(late)
    rebuild_projection(store)

    at_t = occurred + timedelta(days=1)
    result = retrieve(store, "batch size recommendation alpha pipeline", "SOUL", RuntimeConfig(), as_of=at_t)
    assert "evt:late-knowledge" not in result.candidate_pool_ids
    assert "evt:late-knowledge" not in {item.event_id for item in result.selected}


def test_ta3_operators_agree_on_the_same_effective_time(tmp_path: Path) -> None:
    """Cross-operator temporal consistency guard (retrieval / projection / cursor paths)."""

    store, case, _closure, config = _active_closure(tmp_path)
    store.append_event(_future_event("evt:future-consistency"))

    at_t = max(event.timestamp for event in case.events)
    result = retrieve(store, case.query, "SOUL", config, as_of=at_t)
    pool = set(result.candidate_pool_ids) | {item.event_id for item in result.selected}
    assert "evt:future-consistency" not in pool


def test_ta4_ordering_is_deterministic_at_equal_times(tmp_path: Path) -> None:
    store = Store(tmp_path / "g0a_order.sqlite3")
    store.init_schema()
    events = [
        _future_event(f"evt:same-{index}", when=BASE)
        for index in range(3)
    ]
    store.append_events(events)
    first = rebuild_projection(store)
    second = rebuild_projection(store)
    assert first == second


def test_ta5_effective_time_is_part_of_run_identity(tmp_path: Path) -> None:
    """effective_time must be frozen into RuntimeContext identity (and later the receipt)."""

    frozen = BASE + timedelta(days=2)
    context = RuntimeContext(
        run_id="run:g0a",
        timestamp=frozen,
        effective_time=frozen,
        problem_ids=[PROBLEM],
        mode=RuntimeMode.REPLAY,
    )
    assert "effective_time" in context.hash_payload()
    assert context.effective_time == frozen


def test_ta6_time_slices_of_one_episode_share_one_lineage() -> None:
    """All slices/perturbations of one episode keep one episode lineage (no split crossing)."""

    matrix = build_scenario_matrix("fixtures")
    c01 = [item for item in matrix if item.spec.source_case_id == "C01"]
    episode_ids = {item.spec.episode_id for item in c01}
    assert episode_ids == {"fixture:C01"}


def test_ta8_unset_optional_time_fields_keep_the_legacy_envelope_bytes() -> None:
    """ADR E2: with the dual-time fields unset the canonical envelope is byte-identical.

    The expectation is rebuilt here from the legacy field set only, so the test fails if a
    future change starts emitting the optional keys (or any other key) into the envelope.
    """

    event = RawEvent(
        id="evt:legacy-envelope",
        timestamp=BASE,
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="lab_note",
        source_locator="lab/alpha/legacy",
        raw_content="legacy event body",
        project_hints=["alpha"],
        problem_hints=[PROBLEM],
        metadata={"roles": ["direct"]},
    )
    legacy_envelope = {
        "id": "evt:legacy-envelope",
        "timestamp": "2026-09-10T09:00:00Z",
        "actor_id": "alice",
        "actor_type": "human",
        "source_type": "lab_note",
        "source_locator": "lab/alpha/legacy",
        "raw_content": "legacy event body",
        "project_hints": ["alpha"],
        "problem_hints": [PROBLEM],
        "metadata": {"roles": ["direct"]},
    }
    assert event.hash_payload() == legacy_envelope
    assert event.content_hash == sha256_json(legacy_envelope)
    assert event.event_time == BASE
    assert event.knowledge_time == BASE


def test_ta9_known_at_requires_a_trusted_structured_basis() -> None:
    """ADR E3: metadata strings, provider output and LLM proposals cannot set known_at."""

    with pytest.raises(Exception):
        RawEvent(
            id="evt:no-basis",
            timestamp=BASE,
            occurred_at=BASE,
            known_at=BASE + timedelta(days=10),
            actor_id="alice",
            actor_type=ActorType.HUMAN,
            source_type="lab_note",
            source_locator="lab/alpha/x",
            raw_content="late knowledge without provenance",
        )
    accepted = RawEvent(
        id="evt:with-basis",
        timestamp=BASE,
        occurred_at=BASE,
        known_at=BASE + timedelta(days=10),
        knowledge_basis="human-approval",
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="lab_note",
        source_locator="lab/alpha/y",
        raw_content="late knowledge with provenance",
    )
    assert accepted.knowledge_time == BASE + timedelta(days=10)
    assert accepted.event_time == BASE


def test_ta10_storage_round_trip_preserves_the_dual_time_envelope(tmp_path: Path) -> None:
    """A stored event must reconstruct to the same canonical envelope and content hash.

    Found during the G0A-C1 audit: `_event_from_row` rebuilt RawEvent from COLUMNS, so the
    new optional fields were dropped on read and the stored content_hash no longer matched.
    Fixed by additive, idempotent raw_events columns; this test locks the round trip.
    """

    store = Store(tmp_path / "g0a_storage.sqlite3")
    store.init_schema()
    occurred = BASE
    known_later = occurred + timedelta(days=30)
    event = RawEvent(
        id="evt:round-trip",
        timestamp=occurred,
        occurred_at=occurred,
        known_at=known_later,
        knowledge_basis="trusted-ingest",
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="lab_note",
        source_locator="lab/alpha/round-trip",
        raw_content="round trip body",
        problem_hints=[PROBLEM],
    )
    store.append_event(event)
    restored = store.get_event("evt:round-trip")
    assert restored is not None
    assert restored.content_hash == event.content_hash
    assert restored.verify_content_hash()
    assert restored.occurred_at == occurred
    assert restored.known_at == known_later
    assert restored.knowledge_basis == "trusted-ingest"
    assert restored.hash_payload() == event.hash_payload()
    # legacy events keep working through the same path
    legacy = RawEvent(
        id="evt:legacy-row",
        timestamp=occurred,
        actor_id="bob",
        actor_type=ActorType.HUMAN,
        source_type="lab_note",
        source_locator="lab/beta/legacy",
        raw_content="legacy body",
    )
    store.append_event(legacy)
    restored_legacy = store.get_event("evt:legacy-row")
    assert restored_legacy is not None
    assert restored_legacy.content_hash == legacy.content_hash
    assert restored_legacy.occurred_at is None


def test_ta11_visibility_is_monotone_in_effective_time(tmp_path: Path) -> None:
    """G0A-C2: once visible at T, a record stays visible for every later T."""

    store = Store(tmp_path / "g0a_monotone.sqlite3")
    store.init_schema()
    early = _future_event("evt:early", when=BASE)
    late = _future_event("evt:late-known", when=BASE + timedelta(days=1))
    late = late.model_copy(
        update={
            "occurred_at": BASE + timedelta(days=1),
            "known_at": BASE + timedelta(days=40),
            "knowledge_basis": "trusted-ingest",
        }
    )
    store.append_events([early, late])
    from team_soul.temporal import TemporalView

    before = {e.id for e in TemporalView(store, BASE + timedelta(hours=1)).events()}
    middle = {e.id for e in TemporalView(store, BASE + timedelta(days=2)).events()}
    after = {e.id for e in TemporalView(store, BASE + timedelta(days=60)).events()}
    assert before <= middle <= after
    assert before == {"evt:early"}
    assert "evt:late-known" in after


def test_ta12_cross_operator_consistency_at_one_effective_time(tmp_path: Path) -> None:
    """G0A-C3: Retrieve / Projection / ActorView / Reopen must see one historical world."""

    store, case, closure, config = _active_closure(tmp_path)
    future = _future_event("evt:future-meta", metadata={"reopen_problem_ids": [PROBLEM]})
    late = _future_event("evt:late-meta").model_copy(
        update={
            "occurred_at": BASE,
            "known_at": BASE + timedelta(days=40),
            "knowledge_basis": "trusted-ingest",
        }
    )
    store.append_events([future, late])
    at_t = max(event.timestamp for event in case.events)
    excluded = {"evt:future-meta", "evt:late-meta"}

    from team_soul.projection import _ordered_events
    from team_soul.temporal import TemporalView

    assert not excluded & {e.id for e in TemporalView(store, at_t).events()}
    assert not excluded & {e.id for e in _ordered_events(store, as_of=at_t)}
    result = retrieve(store, case.query, "SOUL", config, as_of=at_t)
    assert not excluded & (set(result.candidate_pool_ids) | {i.event_id for i in result.selected})
    _reopened, signals = check_reopen(closure, store.list_events(), effective_time=at_t)
    assert signals == []

    # boundary assessment coverage universe must also see only the admitted history
    from team_soul.operators import _team_boundary_reasons

    team_proposal = ClosureProposal.model_validate(
        {
            **case.closure_proposal.model_dump(mode="json"),
            "level": "TEAM",
            "scope": {
                "label": "team:research",
                "project_ids": ["alpha"],
                "problem_ids": [PROBLEM],
                "scope_basis": ["basis"],
                "actor_coverage": ["alice", "bob"],
                "source_coverage": ["evt_c01_1", "evt_c01_2", "evt_c01_3"],
                "validation_basis": ["basis"],
            },
        }
    )
    from team_soul.contracts import BoundaryAssessment, EvidenceKind, EvidenceRef

    team_proposal = team_proposal.model_copy(
        update={
            "boundary_assessment": BoundaryAssessment(
                included_actor_ids=("alice", "bob"),
                included_source_ids=("evt_c01_1", "evt_c01_2", "evt_c01_3"),
                included_problem_ids=(PROBLEM,),
                scope_basis=("basis",),
                counterevidence_refs=(
                    EvidenceRef(source_event_id="evt_c01_2", evidence_kind=EvidenceKind.CONTRADICT),
                ),
                actor_coverage=1.0,
                source_coverage=1.0,
                problem_coverage=1.0,
                uncertainty="LOW",
                validation_basis=("basis",),
            )
        }
    )
    reasons = _team_boundary_reasons(
        team_proposal, team_proposal.scope, store, config, as_of=at_t
    )
    assert "TEAM_SOURCE_COVERAGE_UNVERIFIED" not in reasons

    # closure evidence lookup must fail closed when the evidence is not yet visible
    from team_soul.operators import evaluate_closure

    early = evaluate_closure(case.closure_proposal, store, config, as_of=at_t)
    assert early.status == "ACTIVE"


def test_ta7_legacy_corpus_is_unchanged(tmp_path: Path) -> None:
    """Zero-drift guard: with defaults, the frozen epoch-2 scenario manifest must not move."""

    manifest = scenario_manifest(build_scenario_matrix("fixtures"))
    assert manifest["manifest_hash"] == EPOCH2_MANIFEST

