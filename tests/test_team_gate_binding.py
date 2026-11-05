"""FR-04 regression: the TEAM gate must be evidence-bound, time-bound and non-vacuous.

Reported by the fresh conformance review (Finding D) and the fresh adversarial review (A8):
  - operators.py read the WHOLE library as the coverage universe, so a TEAM boundary could claim
    actors/sources/problems that appear only in unrelated (or future-dated) events;
  - proposal.problem_id was whitelisted unconditionally, making TEAM_PROBLEM_COVERAGE_UNVERIFIED
    unreachable;
  - BoundaryAssessment.valid_from/valid_to were never compared to the decision time;
  - config.team_level_min_actor_coverage was parsed but never read.

Invariants under test: TEAM is a guarded scope promotion grounded in the closure own evidence,
not a count rule and not a whole-library side effect. The gate must stay reachable for a
well-formed proposal (no over-refusal).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from team_soul.config import RuntimeConfig
from team_soul.contracts import (
    ActorType,
    BoundaryAssessment,
    ClosureLevel,
    ClosureProposal,
    EvidenceKind,
    EvidenceRef,
    RawEvent,
    ReopenCondition,
    Scope,
)
from team_soul.operators import evaluate_closure
from team_soul.storage import Store

BASE_TIME = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _event(event_id: str, actor: str, source: str, problem: str) -> RawEvent:
    return RawEvent(
        id=event_id,
        timestamp=BASE_TIME,
        actor_id=actor,
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator=source,
        raw_content=f"evidence for {problem} from {actor}",
        problem_hints=[problem],
    )


def _store(tmp_path: Path, events: list[RawEvent]) -> Store:
    store = Store(tmp_path / "team_gate.sqlite3")
    store.init_schema()
    store.append_events(events)
    return store


def _ref(event_id: str, kind: EvidenceKind) -> EvidenceRef:
    return EvidenceRef(source_event_id=event_id, evidence_kind=kind)


def _proposal(
    *,
    actors: tuple[str, ...] = ("alice", "bob"),
    sources: tuple[str, ...] = ("src:a", "src:b"),
    problems: tuple[str, ...] = ("problem:p1",),
    problem_id: str = "problem:p1",
    supporting: list[EvidenceRef] | None = None,
    valid_from: datetime | None = BASE_TIME,
    valid_to: datetime | None = None,
) -> ClosureProposal:
    support = supporting if supporting is not None else [_ref("evt:a1", EvidenceKind.SUPPORT)]
    return ClosureProposal(
        problem_id=problem_id,
        proposition="Promote the shared finding to a team-level working position.",
        action="Adopt the position and reopen on contrary evidence.",
        level=ClosureLevel.TEAM,
        scope=Scope(
            label="team:research",
            project_ids=["alpha"],
            problem_ids=[problem_id],
            scope_basis=["declared-team-basis"],
            actor_coverage=list(actors),
            source_coverage=list(sources),
            validation_basis=["declared-validation-basis"],
        ),
        supporting_evidence=support,
        dissent_refs=[_ref("evt:a1", EvidenceKind.CONTRADICT)],
        validation_evidence=[_ref("evt:a1", EvidenceKind.OUTCOME)],
        reopen_conditions=[ReopenCondition(key="new_outcome", description="Reopen on a new outcome.")],
        boundary_assessment=BoundaryAssessment(
            version="boundary.test",
            included_actor_ids=actors,
            included_source_ids=sources,
            included_problem_ids=problems,
            scope_basis=("declared-team-basis",),
            counterevidence_refs=(_ref("evt:a1", EvidenceKind.CONTRADICT),),
            actor_coverage=1.0,
            source_coverage=1.0,
            problem_coverage=1.0,
            uncertainty="LOW",
            validation_basis=("declared-validation-basis",),
            valid_from=valid_from,
            valid_to=valid_to,
        ),
    )


def test_well_formed_team_proposal_is_still_promoted(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_event("evt:a1", "alice", "src:a", "problem:p1"), _event("evt:a2", "bob", "src:b", "problem:p1")],
    )
    closure = evaluate_closure(
        _proposal(supporting=[_ref("evt:a1", EvidenceKind.SUPPORT), _ref("evt:a2", EvidenceKind.OUTCOME)]),
        store,
        RuntimeConfig(),
    )
    assert closure.level == ClosureLevel.TEAM
    assert closure.status == "ACTIVE"


def test_team_actor_coverage_must_come_from_the_closure_own_evidence(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_event("evt:a1", "alice", "src:a", "problem:p1"), _event("evt:a2", "bob", "src:b", "problem:p2")],
    )
    closure = evaluate_closure(_proposal(), store, RuntimeConfig())
    assert closure.level != ClosureLevel.TEAM
    assert "TEAM_ACTOR_COVERAGE_UNVERIFIED" in closure.reason_codes


def test_team_problem_coverage_cannot_be_self_declared(tmp_path: Path) -> None:
    store = _store(tmp_path, [_event("evt:a1", "alice", "src:a", "problem:p1")])
    closure = evaluate_closure(
        _proposal(
            actors=("alice",),
            sources=("src:a",),
            problems=("problem:ghost",),
            problem_id="problem:ghost",
        ),
        store,
        RuntimeConfig(),
    )
    assert closure.level != ClosureLevel.TEAM
    assert "TEAM_PROBLEM_COVERAGE_UNVERIFIED" in closure.reason_codes


def test_team_boundary_window_must_cover_the_decision_time(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_event("evt:a1", "alice", "src:a", "problem:p1"), _event("evt:a2", "bob", "src:b", "problem:p1")],
    )
    closure = evaluate_closure(
        _proposal(
            supporting=[_ref("evt:a1", EvidenceKind.SUPPORT), _ref("evt:a2", EvidenceKind.OUTCOME)],
            valid_from=BASE_TIME + timedelta(days=3000),
        ),
        store,
        RuntimeConfig(),
        as_of=BASE_TIME + timedelta(days=1),
    )
    assert closure.level != ClosureLevel.TEAM
    assert "TEAM_BOUNDARY_NOT_YET_VALID" in closure.reason_codes


def test_expired_team_boundary_is_not_accepted(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_event("evt:a1", "alice", "src:a", "problem:p1"), _event("evt:a2", "bob", "src:b", "problem:p1")],
    )
    closure = evaluate_closure(
        _proposal(
            supporting=[_ref("evt:a1", EvidenceKind.SUPPORT), _ref("evt:a2", EvidenceKind.OUTCOME)],
            valid_from=BASE_TIME - timedelta(days=30),
            valid_to=BASE_TIME - timedelta(days=1),
        ),
        store,
        RuntimeConfig(),
        as_of=BASE_TIME,
    )
    assert closure.level != ClosureLevel.TEAM
    assert "TEAM_BOUNDARY_EXPIRED" in closure.reason_codes


def test_team_promotion_honours_the_declared_actor_minimum(tmp_path: Path) -> None:
    store = _store(tmp_path, [_event("evt:a1", "alice", "src:a", "problem:p1")])
    closure = evaluate_closure(
        _proposal(actors=("alice",), sources=("src:a",)),
        store,
        RuntimeConfig(team_level_min_actor_coverage=2),
    )
    assert closure.level != ClosureLevel.TEAM
    assert "TEAM_ACTOR_COVERAGE_BELOW_MINIMUM" in closure.reason_codes
