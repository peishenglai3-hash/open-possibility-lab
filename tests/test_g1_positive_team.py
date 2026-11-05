"""G1 item 3: positive TEAM scenarios (the guarded gate must be able to open).

The reclassification flagged that the matrix had no positive TEAM case, so a learned
BoundaryPolicy would only ever learn "never TEAM". These two structurally different
proposals are built from the real fixtures and must promote to ACTIVE TEAM through the
full gate (problem-scoped coverage, boundary window, actor minimum, counterevidence).
"""

from __future__ import annotations

from datetime import timezone

from team_soul.config import RuntimeConfig
from team_soul.contracts import (
    BoundaryAssessment,
    ClosureLevel,
    ClosureProposal,
    EvidenceKind,
    EvidenceRef,
    ReopenCondition,
    Scope,
)
from team_soul.ingest import load_fixture
from team_soul.operators import evaluate_closure
from team_soul.storage import Store


def _store_with(case, tmp_path, name):
    store = Store(tmp_path / name)
    store.init_schema()
    store.append_events(case.events)
    return store


def _proposal(case, *, actors, sources, problem, as_of):
    support = EvidenceRef(source_event_id=case.events[0].id, evidence_kind=EvidenceKind.SUPPORT)
    counter = EvidenceRef(source_event_id=case.events[1].id, evidence_kind=EvidenceKind.CONTRADICT)
    validation = EvidenceRef(source_event_id=case.events[-1].id, evidence_kind=EvidenceKind.OUTCOME)
    scope = Scope(
        label="team:research",
        project_ids=["alpha"],
        problem_ids=[problem],
        scope_basis=["shared operating policy"],
        actor_coverage=list(actors),
        source_coverage=list(sources),
        validation_basis=["independent replacement run"],
    )
    return ClosureProposal(
        problem_id=problem,
        proposition="Promote the shared finding to a bounded team-level working position.",
        action="Adopt it and reopen on contrary evidence.",
        level=ClosureLevel.TEAM,
        scope=scope,
        supporting_evidence=[support],
        dissent_refs=[counter],
        validation_evidence=[validation],
        reopen_conditions=[
            ReopenCondition(
                key="new_failed_run",
                description="Reopen if a new run contradicts the promoted position.",
            )
        ],
        boundary_assessment=BoundaryAssessment(
            version="boundary.g1",
            included_actor_ids=tuple(actors),
            included_source_ids=tuple(sources),
            included_problem_ids=(problem,),
            scope_basis=("shared operating policy",),
            counterevidence_refs=(counter,),
            actor_coverage=1.0,
            source_coverage=1.0,
            problem_coverage=1.0,
            uncertainty="LOW",
            validation_basis=("independent replacement run",),
            valid_from=as_of,
        ),
    )


def test_g1_positive_team_case_alpha(tmp_path) -> None:
    case = load_fixture("fixtures/C01_historical_revision.json")
    store = _store_with(case, tmp_path, "g1a.sqlite3")
    as_of = max(event.timestamp for event in case.events)
    proposal = _proposal(
        case,
        actors=("alice", "bob"),
        sources=[event.id for event in case.events],
        problem="problem:c01_batch_size",
        as_of=as_of,
    )
    closure = evaluate_closure(proposal, store, RuntimeConfig(), as_of=as_of)
    assert closure.status == "ACTIVE"
    assert closure.level == ClosureLevel.TEAM
    store.close()


def test_g1_positive_team_case_beta(tmp_path) -> None:
    case = load_fixture("fixtures/C02_theory_overclaim.json")
    store = _store_with(case, tmp_path, "g1b.sqlite3")
    as_of = max(event.timestamp for event in case.events)
    proposal = _proposal(
        case,
        actors=("model-a", "mei"),
        sources=[event.id for event in case.events],
        problem="problem:c02_similarity_agreement",
        as_of=as_of,
    )
    closure = evaluate_closure(proposal, store, RuntimeConfig(), as_of=as_of)
    assert closure.status == "ACTIVE"
    assert closure.level == ClosureLevel.TEAM
    assert "TEAM_SCOPE_DOWNGRADED" not in closure.reason_codes
    store.close()
