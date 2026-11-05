from __future__ import annotations

from .contracts import (
    ControllerDecision,
    ExperimentBudget,
    ExperimentDecision,
    FailureRecord,
)


def decide_experiment(
    budget: ExperimentBudget,
    failure: FailureRecord | None = None,
    *,
    human_gate_required: bool = False,
    escalation_threshold: float = 0.75,
    repeated_failure_stop_count: int = 2,
) -> ExperimentDecision:
    """Map explicit residual evidence and budget state to STOP/CONTINUE/ESCALATE.

    This is a migration seam for the Praxis-style controller. It does not
    discover failures, call a provider, mutate history, or activate a policy.
    """

    if not 0.0 <= escalation_threshold <= 1.0:
        raise ValueError("escalation_threshold must be between 0 and 1")
    if repeated_failure_stop_count < 1:
        raise ValueError("repeated_failure_stop_count must be positive")
    reasons: list[str] = []
    decision = ControllerDecision.CONTINUE
    failure_id = failure.failure_id if failure is not None else None
    if budget.exhausted:
        decision = ControllerDecision.STOP
        reasons.append("BUDGET_EXHAUSTED")
    elif failure is not None and failure.hard_failure:
        decision = ControllerDecision.STOP
        reasons.extend(("HARD_FAILURE", *failure.reason_codes))
    elif failure is not None and not failure.recoverable:
        decision = ControllerDecision.STOP
        reasons.extend(("IRREVERSIBLE_FAILURE", *failure.reason_codes))
    elif (
        failure is not None
        and not failure.new_evidence_available
        and failure.repeat_count >= repeated_failure_stop_count
    ):
        decision = ControllerDecision.STOP
        reasons.extend(("NO_NEW_EVIDENCE_REPEATED", *failure.reason_codes))
    elif human_gate_required:
        decision = ControllerDecision.ESCALATE
        reasons.append("HUMAN_GATE_REQUIRED")
    elif failure is not None and failure.residual_magnitude >= escalation_threshold:
        decision = ControllerDecision.ESCALATE
        reasons.extend(("RESIDUAL_ABOVE_ESCALATION_THRESHOLD", *failure.reason_codes))
    else:
        reasons.append("BUDGET_AVAILABLE")
        if failure is not None:
            reasons.extend(failure.reason_codes)
    return ExperimentDecision(
        decision=decision,
        budget_id=budget.budget_id,
        failure_id=failure_id,
        reason_codes=tuple(dict.fromkeys(reasons)),
        remaining_runs=max(0, budget.max_runs - budget.used_runs),
        remaining_tokens=(
            None
            if budget.max_tokens is None
            else max(0, budget.max_tokens - budget.used_tokens)
        ),
        remaining_cost=(
            None
            if budget.max_cost is None
            else max(0.0, budget.max_cost - budget.used_cost)
        ),
        routing_target=(failure.routing_target if failure is not None else "NONE"),
    )


__all__ = [
    "CandidatePolicy",
    "decide_experiment",
    "ExperimentBudget",
    "ExperimentDecision",
    "FailureRecord",
]
