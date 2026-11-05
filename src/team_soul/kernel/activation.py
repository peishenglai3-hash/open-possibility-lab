from __future__ import annotations

from .contracts import (
    ActivationResult,
    ActivationState,
    PolicySpec,
    PolicyStatus,
    RuntimeContext,
)


def resolve_activation(policy: PolicySpec, context: RuntimeContext) -> ActivationResult:
    """Resolve policy coeffects without invoking a model or mutating state.

    INACTIVE means the policy lifecycle does not permit participation.
    BLOCKED means the policy is lifecycle-eligible but the current context does
    not satisfy its spatial, temporal, evidence, or user-control contract.
    """

    if policy.status != PolicyStatus.ACTIVE:
        return ActivationResult(
            state=ActivationState.INACTIVE,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            context_hash=context.context_hash,
            reasons=(f"POLICY_STATUS_{str(policy.status).upper()}",),
        )

    reasons: list[str] = []
    coeffects = policy.coeffects
    if coeffects.requires_problem and not context.problem_ids:
        reasons.append("MISSING_REQUIRED_PROBLEM")
    if len(context.evidence_classes) < coeffects.min_evidence:
        reasons.append("INSUFFICIENT_EVIDENCE")
    missing_classes = set(coeffects.required_evidence_classes) - set(context.evidence_classes)
    if missing_classes:
        reasons.append("MISSING_EVIDENCE_CLASSES:" + ",".join(sorted(missing_classes)))
    if str(context.mode) not in coeffects.allow_modes:
        reasons.append("DISALLOWED_RUNTIME_MODE")
    if str(context.user_control) in coeffects.forbid_user_control:
        reasons.append("USER_CONTROL_FORBIDS_POLICY")

    return ActivationResult(
        state=ActivationState.BLOCKED if reasons else ActivationState.ACTIVE,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        context_hash=context.context_hash,
        reasons=tuple(reasons),
    )
