from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from .contracts import Contract
from .hashing import sha256_json
from .scenario import ScenarioCase


class FailureScoutWeights(Contract):
    model_config = {"extra": "forbid", "frozen": True}

    arm_disagreement: float = Field(default=0.25, ge=0.0)
    gate_margin_proximity: float = Field(default=0.20, ge=0.0)
    missing_evidence_ratio: float = Field(default=0.15, ge=0.0)
    actor_imbalance: float = Field(default=0.15, ge=0.0)
    temporal_revision_risk: float = Field(default=0.10, ge=0.0)
    transfer_ambiguity: float = Field(default=0.10, ge=0.0)
    rarity: float = Field(default=0.05, ge=0.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "FailureScoutWeights":
        total = sum(self.model_dump(mode="python").values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"FailureScout weights must sum to 1.0, got {total}")
        return self


class FailureCandidate(Contract):
    candidate_id: str
    scenario_id: str
    source_case_id: str
    score: float = Field(ge=0.0, le=1.0)
    signals: dict[str, float] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    evidence_level: str
    environment: str
    selected_rank: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FailureScout:
    """Deterministic prioritization only; it never repairs or promotes policy."""

    def __init__(self, weights: FailureScoutWeights | None = None):
        self.weights = weights or FailureScoutWeights()

    def rank(
        self,
        results: list[dict[str, Any]],
        scenarios: list[ScenarioCase],
        *,
        budget: int = 20,
    ) -> list[FailureCandidate]:
        if budget < 0:
            raise ValueError("FailureScout budget cannot be negative")
        scenario_by_id = {item.spec.scenario_id: item for item in scenarios}
        mechanism_counts = Counter(item.spec.mechanism for item in scenarios)
        candidates: list[FailureCandidate] = []
        for result in results:
            scenario_id = str(result.get("scenario", {}).get("scenario_id", result.get("case_id", "")))
            scenario = scenario_by_id.get(scenario_id)
            if scenario is None:
                continue
            signals = _signals(result, scenario, mechanism_counts)
            score = sum(
                signals[name] * getattr(self.weights, name)
                for name in signals
                if hasattr(self.weights, name)
            )
            reasons = tuple(
                name.upper()
                for name, value in signals.items()
                if value >= 0.5
            )
            candidates.append(
                FailureCandidate(
                    candidate_id=f"failure:{sha256_json({'scenario': scenario_id, 'signals': signals})[:20]}",
                    scenario_id=scenario_id,
                    source_case_id=scenario.spec.source_case_id,
                    score=round(min(1.0, max(0.0, score)), 6),
                    signals={key: round(value, 6) for key, value in signals.items()},
                    reason_codes=reasons,
                    evidence_level=_value(scenario.spec.evidence_level),
                    environment=_value(scenario.spec.environment),
                    selected_rank=0,
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.scenario_id))
        selected: list[FailureCandidate] = []
        for rank, candidate in enumerate(candidates[:budget], start=1):
            selected.append(candidate.model_copy(update={"selected_rank": rank}))
        return selected


def _signals(
    result: dict[str, Any],
    scenario: ScenarioCase,
    mechanism_counts: Counter[str],
) -> dict[str, float]:
    arms = result.get("arms", {})
    coverage = [
        float(payload.get("metrics", {}).get("provenance_coverage", 0.0))
        for payload in arms.values()
    ]
    arm_disagreement = max(coverage) - min(coverage) if len(coverage) > 1 else 0.0
    boundary_values = [
        float(payload.get("metrics", {}).get(key, 0.0))
        for payload in arms.values()
        for key in ("closure_gate_correct", "negative_transfer_rejection")
        if key in payload.get("metrics", {})
    ]
    gate_margin = max((1.0 - abs(value - 0.5) * 2.0 for value in boundary_values), default=0.0)
    soul_metrics = arms.get("SOUL", {}).get("metrics", {})
    missing_evidence = 1.0 - float(soul_metrics.get("provenance_coverage", 0.0))
    actor_ids = {event.actor_id for event in scenario.case.events}
    actor_imbalance = 1.0 - (len(actor_ids) / len(scenario.case.events)) if scenario.case.events else 1.0
    temporal_risk = 1.0 - float(soul_metrics.get("temporal_revision_accuracy", 0.0))
    transfer = result.get("transfer") or {}
    transfer_ambiguity = 1.0 if transfer.get("decision") in {"HOLD", "UNVERIFIED", "CANDIDATE"} else 0.0
    rarity = 1.0 / max(1, mechanism_counts[scenario.spec.mechanism])
    return {
        "arm_disagreement": _bounded(arm_disagreement),
        "gate_margin_proximity": _bounded(gate_margin),
        "missing_evidence_ratio": _bounded(missing_evidence),
        "actor_imbalance": _bounded(actor_imbalance),
        "temporal_revision_risk": _bounded(temporal_risk),
        "transfer_ambiguity": _bounded(transfer_ambiguity),
        "rarity": _bounded(rarity),
    }


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = ["FailureCandidate", "FailureScout", "FailureScoutWeights"]
