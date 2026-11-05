from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from team_soul.contracts import ActorCursor, ActorType, EvidenceKind, EvidenceRef, RawEvent
from team_soul.hashing import sha256_text
from team_soul.kernel import (
    ActivationState,
    CoeffectSpec,
    EffectLedger,
    EffectOperation,
    EffectRecord,
    PolicyKind,
    PolicyRegistry,
    PolicySpec,
    PolicyStatus,
    PolicySandbox,
    RuntimeContext,
    RuntimeMode,
    SandboxDecision,
    UserControl,
    diff_policies,
)
from team_soul.storage import Store


def _source_event() -> RawEvent:
    return RawEvent(
        id="evt:d2-source",
        timestamp=datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="d2/source",
        raw_content="A scoped evidence event for D2.",
        metadata={"evidence_class": "validated"},
    )


def _context(**updates) -> RuntimeContext:
    payload = {
        "run_id": "run:d2",
        "timestamp": datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc),
        "problem_ids": ["problem:d2"],
        "actor_ids": ["alice", "bob"],
        "project_ids": ["project:d2"],
        "evidence_classes": {"validated", "dissent"},
        "mode": RuntimeMode.REPLAY,
        "user_control": UserControl.NORMAL,
    }
    payload.update(updates)
    return RuntimeContext(**payload)


def _policy() -> PolicySpec:
    return PolicySpec(
        policy_id="policy:d2",
        kind=PolicyKind.RETRIEVAL,
        version="1.0.0",
        implementation="deterministic-test-policy",
        coeffects=CoeffectSpec(
            requires_problem=True,
            min_evidence=2,
            required_evidence_classes={"validated"},
            allow_modes={RuntimeMode.REPLAY.value},
        ),
        provenance=(
            EvidenceRef(
                source_event_id="evt:d2-source",
                evidence_kind=EvidenceKind.SUPPORT,
            ),
        ),
    )


def test_runtime_context_hash_is_deterministic_and_immutable() -> None:
    first = _context(evidence_classes={"dissent", "validated"})
    second = _context(evidence_classes={"validated", "dissent"})
    assert first.context_hash == second.context_hash
    with pytest.raises(ValidationError):
        first.problem_ids += ("problem:other",)


def test_runtime_context_hash_carries_observation_cursors_without_iam_fields() -> None:
    cursor = ActorCursor(
        actor_id="alice",
        observation_cursor="2026-09-22T00:00:00+00:00::evt:d2-source",
        observed_event_ids=("evt:d2-source",),
    )
    context = _context(actor_cursors=(cursor,))

    assert context.actor_cursors == (cursor,)
    assert context.hash_payload()["actor_cursors"][0]["actor_id"] == "alice"
    assert not hasattr(cursor, "permissions")
    with pytest.raises(ValidationError):
        cursor.actor_id = "bob"


def test_spatial_coeffect_activation_reports_blocking_reasons() -> None:
    policy = _policy().model_copy(update={"status": PolicyStatus.ACTIVE.value})
    from team_soul.kernel import resolve_activation

    active = resolve_activation(policy, _context())
    assert active.state == ActivationState.ACTIVE

    blocked = resolve_activation(
        policy,
        _context(problem_ids=(), evidence_classes={"validated"}, user_control=UserControl.SILENT),
    )
    assert blocked.state == ActivationState.BLOCKED
    assert "MISSING_REQUIRED_PROBLEM" in blocked.reasons
    assert "INSUFFICIENT_EVIDENCE" in blocked.reasons
    assert "USER_CONTROL_FORBIDS_POLICY" in blocked.reasons

    inactive = resolve_activation(_policy(), _context())
    assert inactive.state == ActivationState.INACTIVE
    assert inactive.reasons == ("POLICY_STATUS_CANDIDATE",)


def test_policy_registry_lifecycle_is_rebuilt_from_raw_history(tmp_path: Path) -> None:
    store = Store(tmp_path / "d2-policy.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    original_hash = source.content_hash
    registry = PolicyRegistry(store)

    candidate = registry.register(_policy())
    assert candidate.status == PolicyStatus.CANDIDATE
    old_policy = _policy().model_copy(
        update={"policy_id": "policy:d2-old", "status": PolicyStatus.ACTIVE.value}
    )
    sandbox_result = PolicySandbox(store).hot_swap(
        old_policy,
        candidate,
        [{"source_event_id": source.id}],
        lambda _policy, _case: None,
    )
    sandboxed = registry.transition(
        candidate.policy_id,
        PolicyStatus.SANDBOXED,
        reason="isolated policy replay passed",
        actor_type=ActorType.TOOL,
        sandbox_result=sandbox_result,
    )
    validated = registry.transition(
        sandboxed.policy_id,
        PolicyStatus.VALIDATED,
        reason="sandbox validation passed",
        actor_type=ActorType.TOOL,
    )
    approved = registry.transition(
        validated.policy_id,
        PolicyStatus.HUMAN_APPROVED,
        reason="reviewed by operator",
    )
    with pytest.raises(PermissionError):
        registry.transition(
            approved.policy_id,
            PolicyStatus.ACTIVE,
            reason="tool must not self-promote",
            actor_type=ActorType.TOOL,
        )
    active = registry.transition(
        approved.policy_id,
        PolicyStatus.ACTIVE,
        reason="human gate passed",
    )
    assert registry.get(active.policy_id).status == PolicyStatus.ACTIVE
    disabled = registry.transition(
        active.policy_id,
        PolicyStatus.DISABLED,
        reason="operator disabled policy",
    )
    assert registry.resolve(disabled.policy_id, _context()).state == ActivationState.INACTIVE
    assert store.get_event(source.id).content_hash == original_hash
    assert [(event.id, event.content_hash) for event in store.list_events()] == [
        (source.id, original_hash)
    ]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute(
            "UPDATE policy_registry SET operation = 'tampered' WHERE policy_id = ?",
            (active.policy_id,),
        )

    rebuilt = PolicyRegistry(store)
    assert [item.model_dump(mode="json") for item in rebuilt.list()] == [
        disabled.model_dump(mode="json")
    ]
    assert rebuilt.resolve(disabled.policy_id, _context()).state == ActivationState.INACTIVE
    store.close()


def test_candidate_lifecycle_requires_sandbox_and_cannot_register_active_policy(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "d9-candidate-lifecycle.sqlite3")
    store.init_schema()
    store.append_event(_source_event())
    registry = PolicyRegistry(store)

    direct_active = _policy().model_copy(update={"status": PolicyStatus.ACTIVE.value})
    with pytest.raises(ValueError, match="DRAFT or CANDIDATE"):
        registry.register(direct_active)
    candidate = _policy().model_copy(update={"policy_id": "policy:d9-candidate"})
    registry.register(candidate)
    with pytest.raises(ValueError, match="invalid policy transition"):
        registry.transition(
            candidate.policy_id,
            PolicyStatus.VALIDATED,
            reason="candidate cannot skip sandbox",
        )

    draft = _policy().model_copy(
        update={"policy_id": "policy:d9-draft", "status": PolicyStatus.DRAFT.value}
    )
    registry.register(draft)
    with pytest.raises(ValueError, match="verified SandboxReplayResult"):
        registry.transition(
            draft.policy_id,
            PolicyStatus.SANDBOXED,
            reason="caller cannot self-assert sandbox completion",
        )
    sandboxed = registry.transition(
        draft.policy_id,
        PolicyStatus.SANDBOXED,
        reason="isolated replay completed",
        actor_type=ActorType.TOOL,
        sandbox_result=PolicySandbox(store).hot_swap(
            _policy().model_copy(
                update={"policy_id": "policy:d9-old", "status": PolicyStatus.ACTIVE.value}
            ),
            draft,
            [{"source_event_id": "evt:d2-source"}],
            lambda _policy, _case: None,
        ),
    )
    validated = registry.transition(
        sandboxed.policy_id,
        PolicyStatus.VALIDATED,
        reason="held-out validation complete",
        actor_type=ActorType.TOOL,
    )
    with pytest.raises(PermissionError, match="approval"):
        registry.transition(
            validated.policy_id,
            PolicyStatus.HUMAN_APPROVED,
            reason="tool cannot approve itself",
            actor_type=ActorType.TOOL,
        )
    approved = registry.transition(
        validated.policy_id,
        PolicyStatus.HUMAN_APPROVED,
        reason="reviewed by human operator",
        actor_type=ActorType.HUMAN,
    )
    active = registry.transition(
        approved.policy_id,
        PolicyStatus.ACTIVE,
        reason="human activation after approval",
        actor_type=ActorType.HUMAN,
    )

    assert active.status == PolicyStatus.ACTIVE
    operations = [
        payload["policy_event"]["operation"]
        for _, payload in store.list_policy_events()
    ]
    assert operations == [
        "register",
        "register",
        "transition",
        "transition",
        "transition",
        "transition",
    ]
    store.close()


def test_revoked_policy_requires_explicit_restore_revalidation_and_human_gate(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "d9-revoked-restore.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    registry = PolicyRegistry(store)
    candidate = _policy().model_copy(update={"policy_id": "policy:d9-revoked"})
    registry.register(candidate)
    sandbox_result = PolicySandbox(store).hot_swap(
        _policy().model_copy(
            update={"policy_id": "policy:d9-revoked-old", "status": PolicyStatus.ACTIVE.value}
        ),
        candidate,
        [{"source_event_id": source.id}],
        lambda _policy, _case: None,
    )
    sandboxed = registry.transition(
        candidate.policy_id,
        PolicyStatus.SANDBOXED,
        reason="isolated replay completed",
        actor_type=ActorType.TOOL,
        sandbox_result=sandbox_result,
    )
    validated = registry.transition(
        sandboxed.policy_id,
        PolicyStatus.VALIDATED,
        reason="evaluator revalidated candidate",
        actor_type=ActorType.TOOL,
    )
    approved = registry.transition(
        validated.policy_id,
        PolicyStatus.HUMAN_APPROVED,
        reason="human review approved candidate",
    )
    active = registry.transition(
        approved.policy_id,
        PolicyStatus.ACTIVE,
        reason="human activation gate passed",
    )
    revoked = registry.transition(
        active.policy_id,
        PolicyStatus.REVOKED,
        reason="operator revoked policy version",
    )
    assert revoked.status == PolicyStatus.REVOKED

    with pytest.raises(ValueError, match="invalid policy transition"):
        registry.transition(
            revoked.policy_id,
            PolicyStatus.ACTIVE,
            reason="direct revoked reactivation must fail",
        )
    with pytest.raises(ValueError, match="only a disabled policy"):
        registry.restore(revoked.policy_id, reason="wrong restore path")
    with pytest.raises(ValueError, match="requires restore request and revalidation"):
        registry.restore_revoked(revoked.policy_id, reason="missing restore evidence")
    with pytest.raises(PermissionError, match="restore request"):
        registry.request_restore(
            revoked.policy_id,
            reason="tool cannot request human restoration",
            actor_type=ActorType.TOOL,
        )

    requested = registry.request_restore(
        revoked.policy_id,
        reason="human requests a new restoration review",
    )
    assert requested.status == PolicyStatus.REVOKED
    with pytest.raises(ValueError, match="requires restore request"):
        registry.restore_revoked(revoked.policy_id, reason="revalidation is still missing")

    revalidated = registry.revalidate_restore(
        revoked.policy_id,
        sandbox_result,
        reason="fresh evaluator receipt verified",
    )
    assert revalidated.status == PolicyStatus.REVOKED
    with pytest.raises(PermissionError, match="explicit human gate"):
        registry.restore_revoked(
            revoked.policy_id,
            reason="tool cannot complete human restoration",
            actor_type=ActorType.TOOL,
        )

    restored = registry.restore_revoked(
        revoked.policy_id,
        reason="human accepts the revalidated restoration",
    )
    assert restored.status == PolicyStatus.ACTIVE
    assert registry.get(revoked.policy_id).status == PolicyStatus.ACTIVE
    operations = [
        payload["policy_event"]["operation"]
        for _, payload in store.list_policy_events()
    ]
    assert operations[-4:] == [
        "transition",
        "restore_request",
        "restore_revalidation",
        "restore_after_revalidation",
    ]
    store.close()


def test_sandbox_receipt_is_trusted_and_bound_to_the_replay_result(tmp_path: Path) -> None:
    store = Store(tmp_path / "d9-receipt.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    registry = PolicyRegistry(store)
    candidate = _policy().model_copy(update={"policy_id": "policy:d9-receipt"})
    registry.register(candidate)
    result = PolicySandbox(store).hot_swap(
        _policy().model_copy(
            update={"policy_id": "policy:d9-receipt-old", "status": PolicyStatus.ACTIVE.value}
        ),
        candidate,
        [{"source_event_id": source.id}],
        lambda _policy, _case: None,
    )
    assert result.receipt is not None
    assert result.receipt.candidate_policy_version == candidate.version
    assert result.receipt.code_hash
    assert result.receipt.suite_hash
    assert result.receipt.corpus_hash
    assert result.receipt.split_hash
    assert result.receipt.config_hash
    assert result.receipt.evaluator_hash
    assert result.receipt.result_hash
    assert result.receipt.signature

    forged = result.model_copy(update={"reason_codes": ("FORGED_BY_CANDIDATE",)})
    with pytest.raises(ValueError, match="not authentic"):
        registry.transition(
            candidate.policy_id,
            PolicyStatus.SANDBOXED,
            reason="forged candidate receipt",
            actor_type=ActorType.TOOL,
            sandbox_result=forged,
        )

    without_receipt = result.model_copy(update={"receipt": None})
    with pytest.raises(ValueError, match="trusted evaluator receipt"):
        registry.transition(
            candidate.policy_id,
            PolicyStatus.SANDBOXED,
            reason="self-attested candidate receipt",
            actor_type=ActorType.TOOL,
            sandbox_result=without_receipt,
        )
    store.close()
    reopened_store = Store(tmp_path / "d9-receipt.sqlite3")
    reopened_registry = PolicyRegistry(reopened_store)
    sandboxed = reopened_registry.transition(
        candidate.policy_id,
        PolicyStatus.SANDBOXED,
        reason="receipt remains authentic after evaluator restart",
        actor_type=ActorType.TOOL,
        sandbox_result=result,
    )
    assert sandboxed.status == PolicyStatus.SANDBOXED
    reopened_store.close()


def test_effect_ledger_is_append_only_and_deactivation_is_rebuildable(tmp_path: Path) -> None:
    store = Store(tmp_path / "d2-effects.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    original_hash = source.content_hash
    ledger = EffectLedger(store)
    effect = EffectRecord(
        effect_id="effect:d2:one",
        policy_id="policy:d2",
        policy_version="1.0.0",
        context_hash=_context().context_hash,
        source_event_ids=(source.id,),
        target_projection="retrieval",
        operation=EffectOperation.ADD,
        before_hash=sha256_text("before"),
        after_hash=sha256_text("after"),
        inverse_spec={"kind": "restore-before"},
        created_at=datetime(2026, 9, 22, 1, 1, tzinfo=timezone.utc),
    )
    ledger.append(effect)
    assert [item.effect_id for item in ledger.active()] == [effect.effect_id]
    before_deactivate = ledger.state_hash()
    compensation = ledger.deactivate(
        effect.effect_id,
        context_hash=_context().context_hash,
        reason="operator disabled policy",
    )
    assert compensation.operation == EffectOperation.DEACTIVATE
    assert ledger.active() == []
    assert before_deactivate != ledger.state_hash()
    assert store.get_event(source.id).content_hash == original_hash
    assert [(event.id, event.content_hash) for event in store.list_events()] == [
        (source.id, original_hash)
    ]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("DELETE FROM effect_ledger")

    rebuilt = EffectLedger(store)
    assert [item.model_dump(mode="json") for item in rebuilt.records()] == [
        item.model_dump(mode="json") for item in ledger.records()
    ]
    assert rebuilt.state_hash() == ledger.state_hash()
    store.close()


def test_d3_policy_diff_fork_restore_and_effect_restore(tmp_path: Path) -> None:
    store = Store(tmp_path / "d3-lifecycle.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    registry = PolicyRegistry(store)
    candidate = registry.register(_policy())
    old_policy = _policy().model_copy(
        update={"policy_id": "policy:d3-old", "status": PolicyStatus.ACTIVE.value}
    )
    sandbox_result = PolicySandbox(store).hot_swap(
        old_policy,
        candidate,
        [{"source_event_id": source.id}],
        lambda _policy, _case: None,
    )
    sandboxed = registry.transition(
        candidate.policy_id,
        PolicyStatus.SANDBOXED,
        reason="D3 sandbox replay passed",
        actor_type=ActorType.TOOL,
        sandbox_result=sandbox_result,
    )
    validated = registry.transition(
        sandboxed.policy_id,
        PolicyStatus.VALIDATED,
        reason="sandbox validation passed",
        actor_type=ActorType.TOOL,
    )
    approved = registry.transition(
        validated.policy_id,
        PolicyStatus.HUMAN_APPROVED,
        reason="reviewed by operator",
    )
    active = registry.transition(
        approved.policy_id,
        PolicyStatus.ACTIVE,
        reason="human gate passed",
    )
    disabled = registry.transition(
        active.policy_id,
        PolicyStatus.DISABLED,
        reason="temporary disable",
    )
    restored = registry.restore(disabled.policy_id, reason="restore after review")
    assert restored.status == PolicyStatus.ACTIVE
    forked = registry.fork(
        restored.policy_id,
        new_policy_id="policy:d2:fork",
        new_version="2.0.0",
        changes={"config": {"threshold": 2}},
        reason="candidate policy fork",
    )
    assert forked.status == PolicyStatus.CANDIDATE
    assert forked.parent_version == restored.version
    diff = diff_policies(restored, forked)
    assert {"policy_id", "version", "config", "status", "parent_version"}.issubset(
        set(diff.changed_fields)
    )
    assert diff.before_hash != diff.after_hash

    ledger = EffectLedger(store)
    effect = EffectRecord(
        effect_id="effect:d3:one",
        policy_id=restored.policy_id,
        policy_version=restored.version,
        context_hash=_context().context_hash,
        source_event_ids=(source.id,),
        target_projection="retrieval",
        operation=EffectOperation.ADD,
        before_hash=sha256_text("before"),
        after_hash=sha256_text("after"),
        created_at=datetime(2026, 9, 22, 1, 1, tzinfo=timezone.utc),
    )
    ledger.append(effect)
    ledger.deactivate(effect.effect_id, context_hash=_context().context_hash, reason="disable")
    assert ledger.active() == []
    restored_effect = ledger.restore(
        effect.effect_id,
        context_hash=_context().context_hash,
        reason="restore",
    )
    assert restored_effect.operation == EffectOperation.ADD
    assert [item.effect_id for item in ledger.active()] == [restored_effect.effect_id]
    assert [event.id for event in store.list_events()] == [source.id]
    store.close()


def test_d3_hot_swap_sandbox_replays_without_raw_mutation(tmp_path: Path) -> None:
    store = Store(tmp_path / "d3-sandbox.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    old = _policy().model_copy(update={"status": PolicyStatus.ACTIVE.value})
    candidate = old.model_copy(
        update={
            "policy_id": "policy:d2:candidate",
            "version": "2.0.0",
            "status": PolicyStatus.CANDIDATE.value,
            "config": {"threshold": 2},
            "parent_version": old.version,
        }
    )

    def effect_factory(policy: PolicySpec, case: dict) -> EffectRecord:
        return EffectRecord(
            effect_id=f"{policy.policy_id}:{case['case_id']}",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            context_hash=_context().context_hash,
            source_event_ids=(source.id,),
            target_projection="retrieval",
            operation=EffectOperation.ADD,
            before_hash=sha256_text("before"),
            after_hash=sha256_text(f"{policy.version}:{case['case_id']}"),
            created_at=datetime(2026, 9, 22, 1, 2, tzinfo=timezone.utc),
        )

    sandbox = PolicySandbox(store)
    result = sandbox.hot_swap(
        old,
        candidate,
        [{"case_id": "case-1"}],
        effect_factory,
    )
    assert result.decision == SandboxDecision.HUMAN_GATE_REQUIRED
    assert result.invariant_regressions == ()
    assert result.raw_history_hash_before == result.raw_history_hash_after
    assert result.old_projection_hash != result.new_projection_hash
    assert result.disabled_projection_hash is not None
    assert result.rebuilt_projection_hash == result.new_projection_hash
    assert [event.id for event in store.list_events()] == [source.id]

    rejected = sandbox.hot_swap(
        old,
        candidate,
        [{"case_id": "case-1"}],
        effect_factory,
        invariant_checker=lambda *_: ("TRANSFER_VETO_REGRESSION",),
    )
    assert rejected.decision == SandboxDecision.REJECT
    assert rejected.invariant_regressions == ("TRANSFER_VETO_REGRESSION",)
    store.close()


def test_d3_temporal_composition_disables_old_and_rebuilds_candidate(tmp_path: Path) -> None:
    store = Store(tmp_path / "d3-temporal.sqlite3")
    store.init_schema()
    source = _source_event()
    store.append_event(source)
    old = _policy().model_copy(update={"status": PolicyStatus.ACTIVE.value})
    candidate = old.model_copy(
        update={
            "policy_id": "policy:d2:temporal-candidate",
            "version": "2.0.0",
            "status": PolicyStatus.CANDIDATE.value,
            "config": {"threshold": 2},
            "parent_version": old.version,
        }
    )

    def effect_factory(policy: PolicySpec, case: dict) -> EffectRecord:
        return EffectRecord(
            effect_id=f"{policy.policy_id}:{case['case_id']}",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            context_hash=_context().context_hash,
            source_event_ids=(source.id,),
            target_projection="retrieval",
            operation=EffectOperation.ADD,
            before_hash=sha256_text("before"),
            after_hash=sha256_text(f"{policy.version}:{case['case_id']}"),
            created_at=datetime(2026, 9, 22, 1, 2, tzinfo=timezone.utc),
        )

    result = PolicySandbox(store).temporal_compose(
        old,
        candidate,
        [{"case_id": "case-1"}],
        effect_factory,
    )
    assert result.decision == SandboxDecision.HUMAN_GATE_REQUIRED
    assert result.old_projection_hash != result.disabled_projection_hash
    assert result.disabled_projection_hash != result.candidate_projection_hash
    assert result.candidate_projection_hash == result.rebuilt_projection_hash
    assert result.raw_history_hash_before == result.raw_history_hash_after
    assert [event.id for event in store.list_events()] == [source.id]
    store.close()
