from datetime import datetime, timezone
from pathlib import Path

from team_soul.contracts import ActorType, RawEvent
from team_soul.kernel import (
    CandidatePolicy,
    ControllerDecision,
    ExperimentBudget,
    FailureRecord,
    PolicyKind,
    PolicyRegistry,
    PolicyRuntime,
    PolicySpec,
    PolicyStatus,
    PolicySandbox,
    RuntimeMode,
    CoeffectSpec,
    decide_experiment,
)
from team_soul.storage import Store


def _source_event() -> RawEvent:
    return RawEvent(
        id="evt:d4-source",
        timestamp=datetime(2026, 9, 22, tzinfo=timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="d4/source",
        raw_content="D4 migration seam source event.",
        metadata={"evidence_class": "validated"},
    )


def _policy() -> PolicySpec:
    return PolicySpec(
        policy_id="policy:d4",
        kind=PolicyKind.RETRIEVAL,
        version="1.0.0",
        implementation="d4-test-policy",
        coeffects=CoeffectSpec(allow_modes={RuntimeMode.REPLAY.value}),
        provenance=({
            "source_event_id": "evt:d4-source",
            "evidence_kind": "support",
        },),
    )


def test_actor_cursor_remains_observation_scope_only(tmp_path: Path) -> None:
    store = Store(tmp_path / "d4-cursor.sqlite3")
    store.init_schema()
    store.append_event(_source_event())
    from team_soul.projection import actor_cursor

    cursor = actor_cursor(store, "alice")
    assert cursor.actor_id == "alice"
    assert cursor.observation_cursor.endswith("::evt:d4-source")
    assert cursor.exposure_cursor is None
    assert not hasattr(cursor, "roles")
    assert not hasattr(cursor, "permissions")
    store.close()


def test_failure_budget_controller_is_deterministic_and_immutable() -> None:
    budget = ExperimentBudget(
        budget_id="budget:d4",
        max_runs=2,
        max_tokens=100,
        max_cost=1.0,
    )
    failure = FailureRecord(
        failure_id="failure:d4:residual",
        run_id="run:d4",
        stage="validate",
        context_hash="ctx:d4",
        residual_magnitude=0.2,
        reason_codes=("MISSING_EVIDENCE",),
    )
    first = decide_experiment(budget, failure)
    assert first.decision == ControllerDecision.CONTINUE
    assert first.reason_codes == ("BUDGET_AVAILABLE", "MISSING_EVIDENCE")
    next_budget = budget.consume(runs=1, tokens=20, cost=0.2)
    escalated = decide_experiment(
        next_budget,
        failure.model_copy(update={"residual_magnitude": 0.9}),
    )
    assert escalated.decision == ControllerDecision.ESCALATE
    assert "RESIDUAL_ABOVE_ESCALATION_THRESHOLD" in escalated.reason_codes
    exhausted = decide_experiment(next_budget.consume(runs=1), failure)
    assert exhausted.decision == ControllerDecision.STOP
    assert "BUDGET_EXHAUSTED" in exhausted.reason_codes
    assert budget.used_runs == 0
    token_exhausted = ExperimentBudget(
        budget_id="budget:d4:tokens",
        max_runs=5,
        max_tokens=10,
        used_tokens=10,
    )
    assert decide_experiment(token_exhausted).decision == ControllerDecision.STOP


def test_failure_controller_stops_repeated_failures_without_new_evidence() -> None:
    budget = ExperimentBudget(budget_id="budget:d4:repeat", max_runs=5)
    failure = FailureRecord(
        failure_id="failure:d4:repeat",
        run_id="run:d4",
        stage="retrieve",
        context_hash="ctx:d4",
        reason_codes=("SAME_RESIDUAL",),
        routing_target="RETRIEVAL",
        new_evidence_available=False,
        repeat_count=2,
        previous_failure_id="failure:d4:previous",
    )

    decision = decide_experiment(budget, failure)

    assert decision.decision == ControllerDecision.STOP
    assert "NO_NEW_EVIDENCE_REPEATED" in decision.reason_codes
    assert decision.routing_target == "RETRIEVAL"


def test_candidate_policy_view_follows_registry_lifecycle_without_new_truth_source(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "d4-candidate.sqlite3")
    store.init_schema()
    store.append_event(_source_event())
    registry = PolicyRegistry(store)
    candidate = registry.register(_policy())
    view = registry.candidate_view(
        candidate.policy_id,
        context_hash="ctx:d4",
        selection_reasons=("TEMPORAL_REPLAY_PASS",),
    )
    assert isinstance(view, CandidatePolicy)
    assert view.status == PolicyStatus.CANDIDATE
    assert view.selection_reasons == ("TEMPORAL_REPLAY_PASS",)
    old_policy = _policy().model_copy(
        update={"policy_id": "policy:d4-old", "status": PolicyStatus.ACTIVE.value}
    )
    sandbox_result = PolicySandbox(store).hot_swap(
        old_policy,
        candidate,
        [{"source_event_id": "evt:d4-source"}],
        lambda _policy, _case: None,
    )
    sandboxed = registry.transition(
        candidate.policy_id,
        PolicyStatus.SANDBOXED,
        reason="D4 isolated replay passed",
        actor_type=ActorType.TOOL,
        sandbox_result=sandbox_result,
    )
    validated = registry.transition(
        sandboxed.policy_id,
        PolicyStatus.VALIDATED,
        reason="D4 migration seam validation",
        actor_type=ActorType.TOOL,
    )
    assert registry.candidate_view(validated.policy_id).status == PolicyStatus.VALIDATED
    approved = registry.transition(
        validated.policy_id,
        PolicyStatus.HUMAN_APPROVED,
        reason="human review",
    )
    approved_view = registry.candidate_view(approved.policy_id)
    assert approved_view.requires_human_gate is True
    active = registry.transition(
        approved.policy_id,
        PolicyStatus.ACTIVE,
        reason="explicit human activation",
    )
    assert registry.candidate_view(active.policy_id).status == PolicyStatus.ACTIVE
    store.close()


def test_policy_runtime_resolves_forked_active_implementation_after_replacement(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "d4-runtime-replacement.sqlite3")
    store.init_schema()
    store.append_event(_source_event())
    registry = PolicyRegistry(store)
    old = registry.register(_policy().model_copy(update={"policy_id": "policy:d4-runtime-old"}))
    old_sandbox = PolicySandbox(store).hot_swap(
        _policy().model_copy(
            update={"policy_id": "policy:d4-runtime-previous", "status": PolicyStatus.ACTIVE.value}
        ),
        old,
        [{"source_event_id": "evt:d4-source"}],
        lambda _policy, _case: None,
    )
    old = registry.transition(
        old.policy_id,
        PolicyStatus.SANDBOXED,
        reason="old implementation sandboxed",
        actor_type=ActorType.TOOL,
        sandbox_result=old_sandbox,
    )
    old = registry.transition(old.policy_id, PolicyStatus.VALIDATED, reason="old validated")
    old = registry.transition(old.policy_id, PolicyStatus.HUMAN_APPROVED, reason="old approved")
    old = registry.transition(old.policy_id, PolicyStatus.ACTIVE, reason="old activated")

    new = registry.fork(
        old.policy_id,
        new_policy_id="policy:d4-runtime-new",
        new_version="2.0.0",
        changes={"implementation": "new-retrieval"},
        reason="replace old retrieval implementation",
    )
    new_sandbox = PolicySandbox(store).hot_swap(
        old,
        new,
        [{"source_event_id": "evt:d4-source"}],
        lambda _policy, _case: None,
    )
    new = registry.transition(
        new.policy_id,
        PolicyStatus.SANDBOXED,
        reason="new implementation sandboxed",
        actor_type=ActorType.TOOL,
        sandbox_result=new_sandbox,
    )
    new = registry.transition(new.policy_id, PolicyStatus.VALIDATED, reason="new validated")
    new = registry.transition(new.policy_id, PolicyStatus.HUMAN_APPROVED, reason="new approved")
    new = registry.transition(new.policy_id, PolicyStatus.ACTIVE, reason="new activated")
    registry.transition(old.policy_id, PolicyStatus.DISABLED, reason="old disabled after replacement")

    old_impl = object()
    new_impl = object()
    runtime = PolicyRuntime(
        registry,
        implementations={"d4-test-policy": old_impl, "new-retrieval": new_impl},
    )

    assert runtime.active_policy(PolicyKind.RETRIEVAL).policy_id == new.policy_id
    assert runtime.bundle().retrieval is new_impl
    assert runtime.manifest()["policies"]["retrieval"]["policy_id"] == new.policy_id
    store.close()
