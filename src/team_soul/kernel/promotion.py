from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import comb
from typing import Literal

from pydantic import ConfigDict, Field

from ..contracts import Contract
from ..storage import Store
from .contracts import ExperimentRun, PromotionDecision, PromotionStatus


class HeldoutPair(Contract):
    """Paired evaluator output; oracle metrics remain outside candidate input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    evidence_level: str
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]
    episode_id: str = ""
    sample_identity: str = ""
    variant_ids: tuple[str, ...] = ()

    @property
    def effective_episode_id(self) -> str:
        return self.episode_id or self.scenario_id


class ExperimentLedger:
    """Append-only storage facade for immutable experiment receipts."""

    def __init__(self, store: Store):
        self.store = store
        self.store.init_schema()

    def append(self, run: ExperimentRun) -> str:
        return self.store.append_experiment_run(run.model_dump(mode="json"))

    def list(self) -> list[ExperimentRun]:
        return [ExperimentRun.model_validate(item) for item in self.store.list_experiment_runs()]


def evaluate_promotion(
    run: ExperimentRun,
    pairs: Sequence[HeldoutPair],
    *,
    objective_directions: Mapping[str, Literal["maximize", "minimize"]],
    invariant_regressions: Sequence[str] = (),
    hard_gate_regressions: Sequence[str] = (),
    minimum_heldout_cases: int = 8,
    minimum_improvement: float = 0.01,
    regression_alpha: float = 0.05,
    human_approved: bool = False,
) -> PromotionDecision:
    """Evaluate a candidate without changing registry, policy, or raw history.

    The paired sign test is intentionally conservative and is descriptive, not
    a substitute for a powered historical evaluation. Small or incomplete
    held-out sets remain HOLD. ``PROMOTE`` is only a recommendation after an
    explicit human approval; this function never activates a candidate.
    """

    if minimum_heldout_cases < 1:
        raise ValueError("minimum_heldout_cases must be positive")
    if minimum_improvement < 0:
        raise ValueError("minimum_improvement cannot be negative")
    if not 0.0 < regression_alpha < 1.0:
        raise ValueError("regression_alpha must be between 0 and 1")
    if not objective_directions:
        raise ValueError("at least one promotion objective is required")

    invariant = tuple(sorted(set(invariant_regressions)))
    hard_gates = tuple(sorted(set(hard_gate_regressions)))
    if invariant or hard_gates:
        return PromotionDecision(
            decision=PromotionStatus.REJECT,
            experiment_run_id=run.run_id,
            invariant_regressions=invariant,
            hard_gate_regressions=hard_gates,
            heldout_results={"status": "BLOCKED_BY_REGRESSION"},
            reason_codes=("INVARIANT_OR_HARD_GATE_REGRESSION",),
        )

    ordered_pairs, deduplication = deduplicate_pairs(pairs)
    missing_metrics = sorted(
        {
            metric
            for metric in objective_directions
            if any(
                metric not in pair.baseline_metrics or metric not in pair.candidate_metrics
                for pair in ordered_pairs
            )
        }
    )
    summaries: dict[str, dict[str, object]] = {}
    improvements: list[str] = []
    significant_regressions: list[str] = []
    material_regressions: list[str] = []

    if not missing_metrics:
        for metric, direction in sorted(objective_directions.items()):
            oriented: list[float] = []
            baseline_values: list[float] = []
            candidate_values: list[float] = []
            for pair in ordered_pairs:
                baseline_value = float(pair.baseline_metrics[metric])
                candidate_value = float(pair.candidate_metrics[metric])
                baseline_values.append(baseline_value)
                candidate_values.append(candidate_value)
                delta = candidate_value - baseline_value
                oriented.append(delta if direction == "maximize" else -delta)

            losses = sum(value < 0 for value in oriented)
            wins = sum(value > 0 for value in oriented)
            non_ties = losses + wins
            p_value = _one_sided_sign_pvalue(losses, non_ties)
            mean_delta = (
                sum(oriented) / len(oriented) if oriented else 0.0
            )
            significant = bool(non_ties and losses > wins and p_value <= regression_alpha)
            meaningful = mean_delta >= minimum_improvement
            if significant:
                significant_regressions.append(metric)
            if mean_delta < 0:
                material_regressions.append(metric)
            if meaningful:
                improvements.append(metric)
            summaries[metric] = {
                "direction": direction,
                "paired_count": len(oriented),
                "baseline_mean": _mean(baseline_values),
                "candidate_mean": _mean(candidate_values),
                "oriented_mean_delta": round(mean_delta, 8),
                "paired_losses": losses,
                "paired_wins": wins,
                "one_sided_sign_p": round(p_value, 8),
                "significant_regression": significant,
                "meaningful_pareto_improvement": meaningful,
            }

    heldout = {
        "status": "PASS" if len(ordered_pairs) >= minimum_heldout_cases else "NOT OBSERVED / OPEN GAP",
        "raw_pair_count": len(pairs),
        "paired_case_count": len(ordered_pairs),
        "unique_episode_count": len(ordered_pairs),
        "deduplicated_variant_count": deduplication["deduplicated_variant_count"],
        "deduplication": deduplication,
        "minimum_required_cases": minimum_heldout_cases,
        "evidence_levels": sorted({pair.evidence_level for pair in ordered_pairs}),
        "paired_scenario_ids": [pair.scenario_id for pair in ordered_pairs],
        "missing_metrics": missing_metrics,
        "objective_summaries": summaries,
        "significance_method": "one-sided exact paired sign test; ties excluded",
        "regression_alpha": regression_alpha,
        "interpretation": (
            "Small held-out samples have low power and cannot establish real-world utility."
            if len(ordered_pairs) < 20
            else "Results are limited to the declared held-out corpus."
        ),
    }

    reasons: list[str] = []
    if significant_regressions:
        reasons.append("SIGNIFICANT_HELDOUT_REGRESSION")
        decision = PromotionStatus.REJECT
    elif len(ordered_pairs) < minimum_heldout_cases:
        reasons.append("INSUFFICIENT_HELDOUT_CASES")
        decision = PromotionStatus.HOLD
    elif missing_metrics:
        reasons.append("INCOMPLETE_HELDOUT_METRICS")
        decision = PromotionStatus.HOLD
    elif material_regressions:
        reasons.append("PARETO_NON_REGRESSION_NOT_MET")
        decision = PromotionStatus.HOLD
    elif not improvements:
        reasons.append("NO_MEANINGFUL_PARETO_IMPROVEMENT")
        decision = PromotionStatus.HOLD
    elif not human_approved:
        reasons.append("HUMAN_APPROVAL_REQUIRED")
        decision = PromotionStatus.ESCALATE
    else:
        reasons.append("DRY_RUN_ELIGIBLE_PENDING_SEPARATE_ACTIVATION")
        decision = PromotionStatus.PROMOTE

    return PromotionDecision(
        decision=decision,
        experiment_run_id=run.run_id,
        pareto_improvements=tuple(improvements),
        heldout_results=heldout,
        reason_codes=tuple(reasons),
    )


def _one_sided_sign_pvalue(losses: int, non_ties: int) -> float:
    if non_ties == 0:
        return 1.0
    return sum(comb(non_ties, count) for count in range(losses, non_ties + 1)) / (2**non_ties)


def deduplicate_pairs(
    pairs: Sequence[HeldoutPair],
) -> tuple[list[HeldoutPair], dict[str, object]]:
    """Collapse derived/perturbed rows and relabelled copies to one promotion unit.

    The evaluator may retain every row for audit, but promotion evidence counts only
    canonical units. FR-07: a unit is the transitive closure of the declared episode
    identity and the content-derived sample identity, so neither a derived variant nor
    a relabelled copy of one sample can manufacture a larger effective N.
    """

    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    keys_by_pair: list[tuple[HeldoutPair, list[str]]] = []
    for pair in pairs:
        keys = [f"episode:{pair.effective_episode_id}"]
        if pair.sample_identity:
            keys.append(f"content:{pair.sample_identity}")
        for key in keys:
            find(key)
        for key in keys[1:]:
            union(keys[0], key)
        keys_by_pair.append((pair, keys))

    grouped: dict[str, list[HeldoutPair]] = {}
    for pair, keys in keys_by_pair:
        grouped.setdefault(find(keys[0]), []).append(pair)
    canonical: list[HeldoutPair] = []
    groups: list[dict[str, object]] = []
    duplicate_count = 0
    for unit_id in sorted(grouped):
        variants = sorted(grouped[unit_id], key=lambda item: _canonical_pair_key(item))
        selected = variants[0]
        variant_ids = tuple(item.scenario_id for item in variants)
        canonical.append(selected.model_copy(update={"variant_ids": variant_ids}))
        duplicate_count += max(0, len(variants) - 1)
        groups.append(
            {
                "episode_id": selected.effective_episode_id,
                "declared_episode_ids": sorted(
                    {item.effective_episode_id for item in variants}
                ),
                "canonical_scenario_id": selected.scenario_id,
                "variant_ids": list(variant_ids),
                "variant_count": len(variants),
            }
        )
    return canonical, {
        "deduplication_unit": (
            "effective_episode_closure(declared episode_id, content sample_identity)"
        ),
        "raw_pair_count": len(pairs),
        "unique_episode_count": len(canonical),
        "deduplicated_variant_count": duplicate_count,
        "groups": groups,
    }


def _canonical_pair_key(pair: HeldoutPair) -> tuple[int, str]:
    # Prefer the unmodified source row when available; otherwise choose a
    # deterministic scenario id. This never changes the evaluator oracle.
    return (0 if "__" not in pair.scenario_id and not pair.scenario_id.startswith("PER-") else 1, pair.scenario_id)


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 8)


__all__ = ["ExperimentLedger", "HeldoutPair", "deduplicate_pairs", "evaluate_promotion"]
