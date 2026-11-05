from __future__ import annotations

import ast
import inspect
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import UNSCORED, Contract, RawEvent
from .hashing import sha256_json
from .scenario import ScenarioCase


class CandidateInput(Contract):
    """The only input a candidate receives for held-out evaluation."""

    model_config = {"extra": "forbid", "frozen": True}

    query: str
    events: tuple[RawEvent, ...]
    context_budget_tokens: int


CandidateRunner = Callable[[CandidateInput], Mapping[str, Any]]


@dataclass(frozen=True)
class ScenarioSplit:
    train: tuple[ScenarioCase, ...]
    heldout: tuple[ScenarioCase, ...]
    seed: int
    split_hash: str
    split_unit: str
    train_episode_ids: tuple[str, ...]
    heldout_episode_ids: tuple[str, ...]
    group_disjoint: bool


def deterministic_split(
    scenarios: Sequence[ScenarioCase],
    *,
    seed: int = 20260922,
    heldout_ratio: float = 0.25,
) -> ScenarioSplit:
    if not 0.0 < heldout_ratio < 1.0:
        raise ValueError("heldout_ratio must be between 0 and 1")
    if len(scenarios) < 2:
        raise ValueError("at least two scenarios are required for a held-out split")
    groups: dict[str, list[ScenarioCase]] = defaultdict(list)
    for scenario in scenarios:
        groups[_episode_id(scenario)].append(scenario)
    ordered_group_ids = sorted(
        groups,
        key=lambda episode_id: sha256_json({"seed": seed, "episode_id": episode_id}),
    )
    heldout_group_count = max(
        1,
        min(len(ordered_group_ids) - 1, round(len(ordered_group_ids) * heldout_ratio)),
    )
    heldout_episode_ids = tuple(sorted(ordered_group_ids[:heldout_group_count]))
    train_episode_ids = tuple(sorted(ordered_group_ids[heldout_group_count:]))
    heldout_set = set(heldout_episode_ids)
    train = tuple(
        sorted(
            (scenario for episode_id in train_episode_ids for scenario in groups[episode_id]),
            key=lambda item: item.spec.scenario_id,
        )
    )
    heldout = tuple(
        sorted(
            (scenario for episode_id in heldout_episode_ids for scenario in groups[episode_id]),
            key=lambda item: item.spec.scenario_id,
        )
    )
    split_hash = sha256_json(
        {
            "seed": seed,
            "split_unit": "episode_id",
            "train_episodes": list(train_episode_ids),
            "heldout_episodes": list(heldout_episode_ids),
            "train": [item.spec.scenario_id for item in train],
            "heldout": [item.spec.scenario_id for item in heldout],
        }
    )
    return ScenarioSplit(
        train=train,
        heldout=heldout,
        seed=seed,
        split_hash=split_hash,
        split_unit="episode_id",
        train_episode_ids=train_episode_ids,
        heldout_episode_ids=heldout_episode_ids,
        group_disjoint=not (set(train_episode_ids) & heldout_set),
    )


class IsolatedHeldoutEvaluator:
    """Evaluator/oracle boundary kept outside the candidate callable."""

    def evaluate(
        self,
        scenarios: Sequence[ScenarioCase],
        candidate: CandidateRunner,
        *,
        baseline: CandidateRunner | None = None,
        candidate_source: str | None = None,
        baseline_source: str | None = None,
        context_budget_tokens: int = 6000,
    ) -> dict[str, Any]:
        candidate_audit = audit_candidate_source(
            candidate_source if candidate_source is not None else _source(candidate)
        )
        baseline_audit = (
            audit_candidate_source(baseline_source if baseline_source is not None else _source(baseline))
            if baseline is not None
            else {"passed": True, "reason_codes": []}
        )
        if not candidate_audit["passed"] or not baseline_audit["passed"]:
            return {
                "status": "REJECTED_CHEATING_RISK",
                "candidate_audit": candidate_audit,
                "baseline_audit": baseline_audit,
                "cases": [],
                "oracle_isolation": False,
            }
        rows: list[dict[str, Any]] = []
        before_hash = _history_hash(scenarios)
        for scenario in scenarios:
            candidate_input = CandidateInput(
                query=scenario.case.query,
                events=tuple(scenario.case.events),
                context_budget_tokens=context_budget_tokens,
            )
            candidate_row = _run(candidate, candidate_input, scenario)
            baseline_row = (
                _run(baseline, candidate_input, scenario) if baseline is not None else None
            )
            oracle = scenario.case.expected
            rows.append(
                {
                    "scenario_id": scenario.spec.scenario_id,
                    "episode_id": _episode_id(scenario),
                    "sample_identity": _sample_identity(scenario),
                    "evidence_level": _value(scenario.spec.evidence_level),
                    "candidate": candidate_row,
                    "baseline": baseline_row,
                    "oracle_metrics": _score(oracle, candidate_row.get("selected_event_ids", [])),
                    "baseline_oracle_metrics": (
                        _score(oracle, baseline_row.get("selected_event_ids", []))
                        if baseline_row is not None
                        else None
                    ),
                }
            )
        after_hash = _history_hash(scenarios)
        return {
            "status": "PASS",
            "case_count": len(rows),
            "cases": rows,
            "candidate_audit": candidate_audit,
            "baseline_audit": baseline_audit,
            "oracle_isolation": True,
            "raw_history_hash_before": before_hash,
            "raw_history_hash_after": after_hash,
            "oracle_hash": sha256_json([scenario.case.expected for scenario in scenarios]),
            "candidate_has_oracle_input": False,
        }


def audit_candidate_source(source: str) -> dict[str, Any]:
    """Static anti-cheating check for oracle access and case special-casing."""

    forbidden_names = {
        "expected",
        "oracle",
        "heldout",
        "held_out",
        "baseline",
        "case_id",
    }
    forbidden_case_literals = re.compile(r"(?:^|[^A-Za-z0-9_])(C\d{2,}|SYN-C\d+|PER-C\d+)")
    reasons: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"passed": False, "reason_codes": ["SOURCE_PARSE_ERROR"]}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.lower() in forbidden_names:
            reasons.append(f"FORBIDDEN_NAME:{node.id}")
        if isinstance(node, ast.Attribute) and node.attr.lower() in forbidden_names:
            reasons.append(f"FORBIDDEN_ATTRIBUTE:{node.attr}")
        if isinstance(node, ast.Attribute) and node.attr == "case_id":
            reasons.append("CASE_ID_SPECIAL_CASING")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() in forbidden_names:
                reasons.append(f"FORBIDDEN_STRING:{node.value}")
            if forbidden_case_literals.search(node.value):
                reasons.append("CASE_ID_LITERAL")
    return {"passed": not reasons, "reason_codes": sorted(set(reasons))}


def _run(
    runner: CandidateRunner,
    candidate_input: CandidateInput,
    scenario: ScenarioCase,
) -> dict[str, Any]:
    try:
        raw = dict(runner(candidate_input))
        selected = raw.get("selected_event_ids", [])
        if not isinstance(selected, list):
            selected = list(selected) if isinstance(selected, (tuple, set)) else []
        allowed = {event.id for event in candidate_input.events}
        return {
            "selected_event_ids": [str(event_id) for event_id in selected if str(event_id) in allowed],
            "raw_output_hash": sha256_json(raw),
            "runner_error": None,
        }
    except Exception as exc:  # candidate failures are evidence, not evaluator failures
        return {
            "selected_event_ids": [],
            "raw_output_hash": "",
            "runner_error": type(exc).__name__,
        }


def _score(expected: Mapping[str, Any], selected: Sequence[str]) -> dict[str, float]:
    selected_set = set(selected)
    required = set(expected.get("required_event_ids", []))
    dissent = set(expected.get("dissent_event_ids", []))
    temporal = set(expected.get("temporal_event_ids", []))
    allowed = set(expected.get("allowed_event_ids", []))
    # Epoch 4, F-2: selecting nothing is not the same as selecting nothing unsupported.
    result = {
        "unsupported_selection_rate": (
            len(selected_set - allowed) / len(selected_set) if selected_set else UNSCORED
        ),
    }
    if required:
        result["provenance_coverage"] = _coverage(required, selected_set)
    if dissent:
        result["dissent_preservation"] = _coverage(dissent, selected_set)
    if temporal:
        result["temporal_revision_accuracy"] = _coverage(temporal, selected_set)
    return result


def _coverage(expected: set[str], actual: set[str]) -> float | str:
    # Epoch 4, F-2: the callers below guard on a non-empty expectation set, so this branch is
    # currently unreachable - but it carried the same defect and must not keep it.
    if not expected:
        return UNSCORED
    return len(expected.intersection(actual)) / len(expected)


def _history_hash(scenarios: Sequence[ScenarioCase]) -> str:
    return sha256_json(
        [
            [(event.id, event.content_hash) for event in scenario.case.events]
            for scenario in scenarios
        ]
    )


def _episode_id(scenario: ScenarioCase) -> str:
    return scenario.spec.episode_id or f"episode:{scenario.spec.source_case_id}"


def _sample_identity(scenario: ScenarioCase) -> str:
    return scenario.spec.sample_identity or sha256_json(
        {
            # FR-07: content identity only. The declared episode label is deliberately
            # excluded so that relabelled copies of one sample share this key.
            "query": scenario.case.query,
            "event_ids": [event.id for event in scenario.case.events],
            "event_hashes": [event.content_hash for event in scenario.case.events],
        }
    )


def _source(runner: CandidateRunner | None) -> str:
    if runner is None:
        return ""
    try:
        return inspect.getsource(runner)
    except (OSError, TypeError):
        return ""


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "CandidateInput",
    "IsolatedHeldoutEvaluator",
    "ScenarioSplit",
    "audit_candidate_source",
    "deterministic_split",
]
