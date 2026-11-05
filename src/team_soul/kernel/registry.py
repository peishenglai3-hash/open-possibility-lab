from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..contracts import ActorType
from ..hashing import sha256_json
from .activation import resolve_activation
from .contracts import (
    ActivationResult,
    CandidatePolicy,
    PolicyDiff,
    PolicySpec,
    PolicyStatus,
    RuntimeContext,
    SandboxDecision,
    SandboxReplayResult,
)
from .receipts import verify_sandbox_receipt

if TYPE_CHECKING:
    from ..storage import Store


_ALLOWED_TRANSITIONS: dict[PolicyStatus, frozenset[PolicyStatus]] = {
    PolicyStatus.DRAFT: frozenset(
        {PolicyStatus.SANDBOXED, PolicyStatus.REJECTED, PolicyStatus.CONTESTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.CANDIDATE: frozenset(
        {PolicyStatus.SANDBOXED, PolicyStatus.CONTESTED, PolicyStatus.REJECTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.SANDBOXED: frozenset(
        {PolicyStatus.VALIDATED, PolicyStatus.CONTESTED, PolicyStatus.REJECTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.VALIDATED: frozenset(
        {PolicyStatus.HUMAN_APPROVED, PolicyStatus.CONTESTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.HUMAN_APPROVED: frozenset(
        {PolicyStatus.ACTIVE, PolicyStatus.CONTESTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.ACTIVE: frozenset(
        {PolicyStatus.DISABLED, PolicyStatus.CONTESTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.DISABLED: frozenset({PolicyStatus.ACTIVE, PolicyStatus.REVOKED}),
    PolicyStatus.CONTESTED: frozenset(
        {PolicyStatus.SANDBOXED, PolicyStatus.REJECTED, PolicyStatus.REVOKED}
    ),
    PolicyStatus.REJECTED: frozenset(),
    PolicyStatus.REVOKED: frozenset(),
}


class PolicyRegistry:
    """Append-only policy lifecycle reconstructed from raw history events."""

    def __init__(self, store: "Store"):
        self.store = store
        self.store.init_schema()

    def list(self) -> list[PolicySpec]:
        policies = self._policies_from_history()
        return [policies[key] for key in sorted(policies)]

    def get(self, policy_id: str) -> PolicySpec | None:
        return self._policies_from_history().get(policy_id)

    def register(
        self,
        policy: PolicySpec,
        *,
        actor_id: str = "team-soul-runtime",
        actor_type: ActorType = ActorType.TOOL,
    ) -> PolicySpec:
        existing = self.get(policy.policy_id)
        if existing is not None:
            if existing.model_dump(mode="json") == policy.model_dump(mode="json"):
                return existing
            raise ValueError(
                f"policy id already registered with a different snapshot: {policy.policy_id}"
            )
        if PolicyStatus(policy.status) not in {PolicyStatus.DRAFT, PolicyStatus.CANDIDATE}:
            raise ValueError("new policies must be registered as DRAFT or CANDIDATE")
        self._require_provenance(policy)
        self._append_policy_event(
            operation="register",
            policy=policy,
            actor_id=actor_id,
            actor_type=actor_type,
            reason="policy registered",
        )
        return policy

    def transition(
        self,
        policy_id: str,
        status: PolicyStatus,
        *,
        reason: str,
        actor_id: str = "human:operator",
        actor_type: ActorType = ActorType.HUMAN,
        sandbox_result: SandboxReplayResult | None = None,
    ) -> PolicySpec:
        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        target = PolicyStatus(status)
        if target == current.status:
            return current
        if target == PolicyStatus.SANDBOXED:
            if sandbox_result is None:
                raise ValueError("SANDBOXED transition requires a verified SandboxReplayResult")
            _validate_sandbox_result(policy_id, current.version, sandbox_result, store=self.store)
            reason = f"{reason}; sandbox_result_hash={sha256_json(sandbox_result.model_dump(mode='json'))}"
        elif sandbox_result is not None:
            raise ValueError("sandbox_result is only valid for a SANDBOXED transition")
        current_status = PolicyStatus(current.status)
        if target not in _ALLOWED_TRANSITIONS[current_status]:
            raise ValueError(
                f"invalid policy transition {current.status!s} -> {target!s} for {policy_id}"
            )
        if target in {PolicyStatus.HUMAN_APPROVED, PolicyStatus.ACTIVE} and actor_type != ActorType.HUMAN:
            action = "approval" if target == PolicyStatus.HUMAN_APPROVED else "activation"
            raise PermissionError(f"policy {action} requires an explicit human gate")
        updated = PolicySpec.model_validate(
            {
                **current.model_dump(mode="json"),
                "status": target.value,
            }
        )
        self._append_policy_event(
            operation="transition",
            policy=updated,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
            previous_status=current_status.value,
        )
        return updated

    def resolve(self, policy_id: str, context: RuntimeContext) -> ActivationResult:
        policy = self.get(policy_id)
        if policy is None:
            raise KeyError(f"policy not found: {policy_id}")
        return resolve_activation(policy, context)

    def restore(
        self,
        policy_id: str,
        *,
        reason: str,
        actor_id: str = "human:operator",
        actor_type: ActorType = ActorType.HUMAN,
    ) -> PolicySpec:
        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        if PolicyStatus(current.status) != PolicyStatus.DISABLED:
            raise ValueError("only a disabled policy can be restored")
        if actor_type != ActorType.HUMAN:
            raise PermissionError("policy restore requires an explicit human gate")
        restored = PolicySpec.model_validate(
            {
                **current.model_dump(mode="json"),
                "status": PolicyStatus.ACTIVE.value,
            }
        )
        self._append_policy_event(
            operation="restore",
            policy=restored,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
            previous_status=PolicyStatus.DISABLED.value,
        )
        return restored

    def request_restore(
        self,
        policy_id: str,
        *,
        reason: str,
        actor_id: str = "human:operator",
        actor_type: ActorType = ActorType.HUMAN,
    ) -> PolicySpec:
        """Open an explicit restore request for a revoked policy version."""

        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        if PolicyStatus(current.status) != PolicyStatus.REVOKED:
            raise ValueError("restore request is only valid for a revoked policy")
        if actor_type != ActorType.HUMAN:
            raise PermissionError("restore request requires an explicit human gate")
        self._append_policy_event(
            operation="restore_request",
            policy=current,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
            previous_status=PolicyStatus.REVOKED.value,
        )
        return current

    def revalidate_restore(
        self,
        policy_id: str,
        sandbox_result: SandboxReplayResult,
        *,
        reason: str,
        actor_id: str = "team-soul-evaluator",
        actor_type: ActorType = ActorType.TOOL,
    ) -> PolicySpec:
        """Record evaluator revalidation while keeping the policy revoked."""

        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        if PolicyStatus(current.status) != PolicyStatus.REVOKED:
            raise ValueError("restore revalidation is only valid for a revoked policy")
        if not self._restore_requested(policy_id):
            raise ValueError("restore revalidation requires a prior restore request")
        _validate_sandbox_result(policy_id, current.version, sandbox_result, store=self.store)
        self._append_policy_event(
            operation="restore_revalidation",
            policy=current,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=f"{reason}; sandbox_result_hash={sha256_json(sandbox_result.model_dump(mode='json'))}",
            previous_status=PolicyStatus.REVOKED.value,
        )
        return current

    def restore_revoked(
        self,
        policy_id: str,
        *,
        reason: str,
        actor_id: str = "human:operator",
        actor_type: ActorType = ActorType.HUMAN,
    ) -> PolicySpec:
        """Restore a revoked version only through its explicit gated sequence."""

        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        if PolicyStatus(current.status) != PolicyStatus.REVOKED:
            raise ValueError("only a revoked policy can use restore_revoked")
        if actor_type != ActorType.HUMAN:
            raise PermissionError("revoked policy restore requires an explicit human gate")
        if not self._restore_revalidated(policy_id):
            raise ValueError("revoked policy requires restore request and revalidation")
        restored = PolicySpec.model_validate(
            {
                **current.model_dump(mode="json"),
                "status": PolicyStatus.ACTIVE.value,
            }
        )
        self._append_policy_event(
            operation="restore_after_revalidation",
            policy=restored,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
            previous_status=PolicyStatus.REVOKED.value,
        )
        return restored

    def fork(
        self,
        policy_id: str,
        *,
        new_policy_id: str,
        new_version: str,
        changes: dict[str, Any] | None = None,
        reason: str,
        actor_id: str = "team-soul-runtime",
        actor_type: ActorType = ActorType.TOOL,
    ) -> PolicySpec:
        current = self.get(policy_id)
        if current is None:
            raise KeyError(f"policy not found: {policy_id}")
        changes = dict(changes or {})
        forbidden = {"policy_id", "version", "status", "parent_version"}
        illegal = forbidden.intersection(changes)
        if illegal:
            raise ValueError(
                "fork changes cannot override lifecycle identity: " + ", ".join(sorted(illegal))
            )
        if self.get(new_policy_id) is not None:
            raise ValueError(f"fork target policy id already exists: {new_policy_id}")
        payload = {
            **current.model_dump(mode="json"),
            **changes,
            "policy_id": new_policy_id,
            "version": new_version,
            "status": PolicyStatus.CANDIDATE.value,
            "parent_version": current.version,
        }
        forked = PolicySpec.model_validate(payload)
        self._require_provenance(forked)
        self._append_policy_event(
            operation="fork",
            policy=forked,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
            previous_status=current.version,
        )
        return forked

    def diff(self, left_policy_id: str, right: PolicySpec) -> PolicyDiff:
        left = self.get(left_policy_id)
        if left is None:
            raise KeyError(f"policy not found: {left_policy_id}")
        return diff_policies(left, right)

    def candidate_view(
        self,
        policy_id: str,
        *,
        context_hash: str = "",
        selection_reasons: tuple[str, ...] = (),
    ) -> CandidatePolicy:
        """Return a CandidatePolicy view without creating a second truth store."""

        policy = self.get(policy_id)
        if policy is None:
            raise KeyError(f"policy not found: {policy_id}")
        return CandidatePolicy.from_policy(
            policy,
            context_hash=context_hash,
            selection_reasons=selection_reasons,
        )

    def _require_provenance(self, policy: PolicySpec) -> None:
        missing = [
            ref.source_event_id
            for ref in policy.provenance
            if self.store.get_event(ref.source_event_id) is None
        ]
        if missing:
            raise ValueError(
                "policy provenance refers to missing raw events: " + ", ".join(sorted(missing))
            )

    def _policies_from_history(self) -> dict[str, PolicySpec]:
        policies: dict[str, PolicySpec] = {}
        for _, payload in self._policy_history():
            if not isinstance(payload, dict):
                continue
            policy_event = payload.get("policy_event")
            if isinstance(policy_event, dict) and isinstance(policy_event.get("policy"), dict):
                policy = PolicySpec.model_validate(policy_event["policy"])
                policies[policy.policy_id] = policy
        for policy_id, policy in self._legacy_policies().items():
            policies.setdefault(policy_id, policy)
        return policies

    def _policy_history(self) -> list[tuple[datetime, dict[str, object]]]:
        """Authoritative lifecycle rows: the append-only control table only.

        Raw-event metadata is candidate-writable, so it must never be able to
        change a policy lifecycle state (FR-01). Folding it back into the
        lifecycle history let a single appended event resurrect a REVOKED policy
        or fabricate restore operations. Only the control table written by the
        gated registry API is consulted here.
        """

        rows = list(self.store.list_policy_events())
        return sorted(rows, key=lambda item: (item[0], sha256_json(item[1])))

    def _legacy_policies(self) -> dict[str, PolicySpec]:
        """Migration view of pre-control-table policy definitions (FR-01).

        A policy the control table has never seen may be admitted from raw-event
        metadata, but only in DRAFT and only when the payload itself declares
        DRAFT or CANDIDATE. Such an entry is inert: it cannot express a
        transition, cannot be restored, and never resolves as an active policy.
        """

        legacy: dict[str, PolicySpec] = {}
        for event in self.store.list_events():
            payload = event.metadata.get("policy_event")
            if not isinstance(payload, dict):
                continue
            raw_policy = payload.get("policy")
            if not isinstance(raw_policy, dict):
                continue
            try:
                policy = PolicySpec.model_validate(raw_policy)
            except ValueError:
                continue
            if PolicyStatus(policy.status) not in {PolicyStatus.DRAFT, PolicyStatus.CANDIDATE}:
                continue
            legacy[policy.policy_id] = policy.model_copy(
                update={"status": PolicyStatus.DRAFT.value}
            )
        return legacy

    def _restore_operations(self, policy_id: str) -> list[str]:
        operations: list[str] = []
        for _, payload in self._policy_history():
            policy_event = payload.get("policy_event") if isinstance(payload, dict) else None
            if not isinstance(policy_event, dict):
                continue
            policy_payload = policy_event.get("policy")
            if not isinstance(policy_payload, dict) or policy_payload.get("policy_id") != policy_id:
                continue
            operations.append(str(policy_event.get("operation", "")))
        return operations

    def _restore_requested(self, policy_id: str) -> bool:
        operations = self._restore_operations(policy_id)
        return "restore_request" in operations and (
            not operations or operations[-1] == "restore_request"
        )

    def _restore_revalidated(self, policy_id: str) -> bool:
        operations = self._restore_operations(policy_id)
        try:
            request_index = max(index for index, value in enumerate(operations) if value == "restore_request")
            validation_index = max(
                index for index, value in enumerate(operations) if value == "restore_revalidation"
            )
        except ValueError:
            return False
        return validation_index > request_index and operations[-1] == "restore_revalidation"

    def _append_policy_event(
        self,
        *,
        operation: str,
        policy: PolicySpec,
        actor_id: str,
        actor_type: ActorType,
        reason: str,
        previous_status: str | None = None,
    ) -> None:
        policy_event: dict[str, object] = {
            "operation": operation,
            "policy": policy.model_dump(mode="json"),
            "reason": reason,
        }
        if previous_status is not None:
            policy_event["previous_status"] = previous_status
        payload = {"event_kind": "policy_registry", "policy_event": policy_event}
        self.store.append_policy_event(
            payload,
            timestamp=self._next_timestamp(),
            policy_id=policy.policy_id,
            operation=operation,
            actor_id=actor_id,
            actor_type=ActorType(actor_type).value,
        )

    def _next_timestamp(self) -> datetime:
        """Keep lifecycle transitions ordered when the clock has coarse resolution."""

        now = datetime.now(timezone.utc)
        timestamps = [event.timestamp for event in self.store.list_events()]
        timestamps.extend(timestamp for timestamp, _ in self.store.list_policy_events())
        if not timestamps:
            return now
        latest = max(timestamps)
        return max(now, latest + timedelta(microseconds=1))


def _validate_sandbox_result(
    policy_id: str, policy_version: str, result: SandboxReplayResult, *, store=None
) -> None:
    if result.candidate_policy_id != policy_id:
        raise ValueError("sandbox result belongs to a different candidate policy")
    if result.decision != SandboxDecision.HUMAN_GATE_REQUIRED:
        raise ValueError("only a non-regressing sandbox replay can mark a candidate SANDBOXED")
    if result.invariant_regressions:
        raise ValueError("sandbox result contains invariant regressions")
    if result.raw_history_hash_before != result.raw_history_hash_after:
        raise ValueError("sandbox result shows raw-history mutation")
    if not result.rebuilt_projection_hash or result.new_projection_hash != result.rebuilt_projection_hash:
        raise ValueError("sandbox result lacks a matching clean projection rebuild")
    receipt = result.receipt
    if receipt is None:
        raise ValueError("sandbox result lacks a trusted evaluator receipt")
    required_receipt_fields = (
        receipt.code_hash,
        receipt.suite_hash,
        receipt.corpus_hash,
        receipt.split_hash,
        receipt.config_hash,
        receipt.evaluator_hash,
        receipt.run_id,
        receipt.result_hash,
        receipt.signature,
    )
    if not all(required_receipt_fields):
        raise ValueError("sandbox receipt is missing trusted binding fields")
    if not verify_sandbox_receipt(
        result,
        receipt,
        candidate_policy_id=policy_id,
        candidate_policy_version=policy_version,
        store=store,
    ):
        raise ValueError("sandbox evaluator receipt is not authentic for this result")


def diff_policies(left: PolicySpec, right: PolicySpec) -> PolicyDiff:
    """Return a deterministic top-level policy diff without applying either policy."""

    before = left.model_dump(mode="json")
    after = right.model_dump(mode="json")
    changed = tuple(sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key)))
    return PolicyDiff(
        policy_id=left.policy_id,
        from_version=left.version,
        to_version=right.version,
        changed_fields=changed,
        before_hash=sha256_json(before),
        after_hash=sha256_json(after),
    )
