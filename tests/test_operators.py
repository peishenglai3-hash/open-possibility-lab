from pathlib import Path
from datetime import datetime, timezone

import pytest

from team_soul.config import RuntimeConfig
from team_soul.contracts import (
    BoundaryAssessment,
    ClosureProposal,
    ClosureLevel,
    ClosureStatus,
    EvidenceKind,
    EvidenceRef,
    RawEvent,
    ReopenCondition,
    Scope,
    TransferCandidate,
)
from team_soul.ingest import load_fixture
from team_soul.operators import (
    check_reopen,
    check_transfer,
    evaluate_closure,
    persist_closure_proposal,
    persist_reopen,
    revoke_closure,
    validate_closure,
)
from team_soul.projection import actor_cursor, load_closures, rebuild_projection, replay_projection
from team_soul.storage import Store


def _store_for(case_path: str, tmp_path: Path):
    case = load_fixture(Path(case_path))
    store = Store(tmp_path / "case.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    rebuild_projection(store)
    return case, store


def test_hard_negative_transfer_is_rejected(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C04_guarded_transfer.json", tmp_path)
    result = check_transfer(case.transfer)
    assert result.decision == "REJECT"
    assert "FORBIDDEN_CONDITION" in result.reason_codes
    store.close()


def test_closure_requires_validation_and_blocks_team_overreach(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C05_no_closure_own_model.json", tmp_path)
    result = evaluate_closure(case.closure_proposal, store, RuntimeConfig())
    assert result.status == "NO_CLOSURE"
    assert "MISSING_VALIDATION_EVIDENCE" in result.reason_codes
    assert "INSUFFICIENT_ACTOR_COVERAGE_FOR_TEAM" in result.reason_codes
    store.close()


def test_reopen_is_revocable(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C01_historical_revision.json", tmp_path)
    closure = evaluate_closure(case.closure_proposal, store, RuntimeConfig())
    reopened, signals = check_reopen(
        closure,
        case.events,
        effective_time=max(event.timestamp for event in case.events),
    )
    assert reopened.status == "ACTIVE"
    assert signals == []
    trigger_event = case.events[-1].model_copy(
        update={
            "id": "evt_reopen",
            "metadata": {
                "reopen_problem_ids": [closure.problem_id],
                "reopen_trigger": "OUTCOME_FAILURE",
            },
        }
    )
    reopened, signals = check_reopen(
        closure, [trigger_event], effective_time=trigger_event.timestamp
    )
    assert reopened.status == "REOPENED"
    assert signals[0].trigger_type == "OUTCOME_FAILURE"
    store.close()


def test_external_veto_is_binding_even_with_shared_conditions() -> None:
    candidate = TransferCandidate(
        source_case_id="source",
        target_case_id="target",
        shared_conditions=[],
        evidence_refs=[
            EvidenceRef(
                source_event_id="evt:veto",
                evidence_kind=EvidenceKind.VETO,
            )
        ],
    )
    result = check_transfer(candidate)
    assert result.decision == "REJECT"
    assert "EXTERNAL_VETO_BINDING" in result.reason_codes


def test_team_promotion_requires_explicit_basis_not_a_people_count(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C01_historical_revision.json", tmp_path)
    proposal = ClosureProposal.model_validate(
        {
            **case.closure_proposal.model_dump(mode="json"),
            "level": "TEAM",
            "scope": {
                "label": "team:research",
                "project_ids": ["alpha"],
                "problem_ids": ["problem:c01_batch_size"],
                "scope_basis": ["shared alpha operating policy"],
                "actor_coverage": ["alice", "bob"],
                "source_coverage": ["evt_c01_1", "evt_c01_2", "evt_c01_3"],
                "counterevidence_refs": ["evt_c01_2"],
                "validation_basis": ["independent replacement run"],
            },
            "boundary_assessment": BoundaryAssessment(
                included_actor_ids=("alice", "bob"),
                excluded_actor_ids=("model-a",),
                included_source_ids=("evt_c01_1", "evt_c01_2", "evt_c01_3"),
                included_problem_ids=("problem:c01_batch_size",),
                scope_basis=("shared alpha operating policy",),
                counterevidence_refs=(
                    EvidenceRef(
                        source_event_id="evt_c01_2",
                        evidence_kind=EvidenceKind.CONTRADICT,
                    ),
                ),
                actor_coverage=1.0,
                source_coverage=1.0,
                problem_coverage=1.0,
                uncertainty="MEDIUM",
                validation_basis=("independent replacement run",),
                valid_from=datetime(2026, 9, 13, tzinfo=timezone.utc),
            ).model_dump(mode="json"),
        }
    )
    result = evaluate_closure(proposal, store, RuntimeConfig())
    assert result.status == "ACTIVE"
    assert result.level == "TEAM"
    store.close()


def test_team_without_boundary_assessment_is_downgraded_to_project_scope(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C01_historical_revision.json", tmp_path)
    proposal = ClosureProposal.model_validate(
        {
            **case.closure_proposal.model_dump(mode="json"),
            "level": "TEAM",
            "scope": {
                "label": "team:research",
                "project_ids": ["alpha"],
                "problem_ids": ["problem:c01_batch_size"],
                "scope_basis": ["shared alpha operating policy"],
                "actor_coverage": ["alice", "bob"],
                "source_coverage": ["evt_c01_1", "evt_c01_2", "evt_c01_3"],
                "counterevidence_refs": ["evt_c01_2"],
                "validation_basis": ["independent replacement run"],
            },
        }
    )

    result = evaluate_closure(proposal, store, RuntimeConfig())

    assert result.status == ClosureStatus.ACTIVE
    assert result.level == ClosureLevel.PROJECT
    assert "TEAM_SCOPE_DOWNGRADED" in result.reason_codes
    assert "TEAM_BOUNDARY_ASSESSMENT_REQUIRED" in result.reason_codes
    store.close()


def test_closure_lifecycle_is_persisted_and_rebuilt_from_history(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C01_historical_revision.json", tmp_path)
    proposed = persist_closure_proposal(case.closure_proposal, store, RuntimeConfig())
    assert load_closures(store)[0].status == "PROPOSED"
    validated = validate_closure(case.closure_proposal, store, RuntimeConfig())
    assert validated.status == "ACTIVE"
    assert load_closures(store)[0].status == "ACTIVE"

    trigger = RawEvent(
        id="evt:closure-reopen",
        timestamp=datetime(2026, 9, 13, tzinfo=timezone.utc),
        actor_id="alice",
        actor_type="human",
        source_type="outcome",
        source_locator="lab/alpha/reopen",
        raw_content="The replacement run failed again.",
        problem_hints=[case.closure_proposal.problem_id],
        metadata={
            "reopen_problem_ids": [case.closure_proposal.problem_id],
            "reopen_trigger": "OUTCOME_FAILURE",
        },
    )
    reopened, signals = persist_reopen(
        proposed.id, store, [trigger], effective_time=trigger.timestamp
    )
    assert signals and reopened.status == "REOPENED"
    assert load_closures(store)[0].status == "REOPENED"
    revoked = revoke_closure(store, proposed.id, "human review superseded this closure")
    assert revoked.status == "REVOKED"
    assert load_closures(store)[0].status == "REVOKED"
    assert len(store.list_events()) == len(case.events) + 5
    store.close()


def test_revoked_closure_cannot_be_reactivated_by_repeating_validation(tmp_path: Path) -> None:
    store = Store(tmp_path / "revoked-closure.sqlite3")
    store.init_schema()
    source = RawEvent(
        id="evt:closure-revoked",
        timestamp=datetime(2026, 9, 13, tzinfo=timezone.utc),
        actor_id="alice",
        actor_type="human",
        source_type="test",
        source_locator="test/closure-revoked",
        raw_content="A bounded closure was proposed and later revoked.",
    )
    store.append_event(source)
    proposal = ClosureProposal(
        problem_id="problem:closure",
        proposition="Use the bounded closure.",
        level=ClosureLevel.PROJECT,
        scope=Scope(label="project:alpha", project_ids=["alpha"], problem_ids=["problem:closure"]),
        supporting_evidence=[
            EvidenceRef(source_event_id=source.id, evidence_kind=EvidenceKind.SUPPORT)
        ],
        validation_evidence=[
            EvidenceRef(source_event_id=source.id, evidence_kind=EvidenceKind.OUTCOME)
        ],
        reopen_conditions=[ReopenCondition(key="failure", description="reopen on failure")],
    )
    created = persist_closure_proposal(proposal, store, RuntimeConfig())
    active = validate_closure(proposal, store, RuntimeConfig())
    assert active.status == ClosureStatus.ACTIVE
    revoked = revoke_closure(store, created.id, "operator revoked version")
    assert revoked.status == ClosureStatus.REVOKED
    with pytest.raises(ValueError, match="revoked closure versions are terminal"):
        validate_closure(proposal, store, RuntimeConfig())
    assert next(item for item in load_closures(store) if item.id == created.id).status == ClosureStatus.REVOKED
    store.close()


def test_actor_cursor_and_as_of_replay_are_deterministic(tmp_path: Path) -> None:
    case, store = _store_for("fixtures/C01_historical_revision.json", tmp_path)
    cursor = actor_cursor(
        store,
        "alice",
        as_of=datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc),
    )
    assert cursor.observed_event_ids == ("evt_c01_1",)
    assert cursor.exposure_cursor is None
    first = replay_projection(
        store,
        as_of=datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc),
    )
    second = replay_projection(
        store,
        as_of=datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc),
    )
    assert first == second
    assert first["event_count"] == 2
    store.close()
