from __future__ import annotations

import inspect
from pathlib import Path

import pytest

if not Path("docs/EVAL_RESULTS_V02.json").is_file():
    pytest.skip(
        "the Week02 evaluation report is not distributed in the public snapshot",
        allow_module_level=True,
    )

from team_soul.config import RuntimeConfig
from team_soul.eval_d6 import run_d6_suite
from team_soul.failure_scout import FailureScout
from team_soul.heldout import (
    CandidateInput,
    IsolatedHeldoutEvaluator,
    audit_candidate_source,
    deterministic_split,
)
from team_soul.scenario import build_scenario_matrix


def _safe_candidate(candidate_input: CandidateInput) -> dict[str, object]:
    return {"selected_event_ids": [candidate_input.events[0].id]}


def _cheating_candidate(candidate_input: CandidateInput) -> dict[str, object]:
    return {"selected_event_ids": [candidate_input.expected["required_event_ids"][0]]}


def test_d6_split_is_disjoint_and_deterministic() -> None:
    scenarios = build_scenario_matrix("fixtures")
    first = deterministic_split(scenarios)
    second = deterministic_split(scenarios)

    first_train_ids = {item.spec.scenario_id for item in first.train}
    first_heldout_ids = {item.spec.scenario_id for item in first.heldout}
    assert first_train_ids.isdisjoint(first_heldout_ids)
    assert first.split_hash == second.split_hash
    assert [item.spec.scenario_id for item in first.train] == [
        item.spec.scenario_id for item in second.train
    ]
    assert [item.spec.scenario_id for item in first.heldout] == [
        item.spec.scenario_id for item in second.heldout
    ]
    assert first.split_unit == "episode_id"
    assert first.group_disjoint is True
    assert set(first.train_episode_ids).isdisjoint(first.heldout_episode_ids)
    assert {
        item.spec.episode_id for item in first.train
    }.isdisjoint({item.spec.episode_id for item in first.heldout})


def test_failure_scout_respects_budget_and_stable_ranking() -> None:
    scenarios = build_scenario_matrix("fixtures")
    result_rows = [
        {
            "scenario": item.spec.model_dump(mode="json"),
            "arms": {
                "BASE": {"metrics": {"provenance_coverage": 0.0}},
                "RAG": {"metrics": {"provenance_coverage": 0.5}},
                "SOUL": {
                    "metrics": {
                        "provenance_coverage": 0.0,
                        "temporal_revision_accuracy": 0.0,
                    }
                },
            },
            "transfer": {"decision": "HOLD"},
        }
        for item in scenarios
    ]
    scout = FailureScout()
    first = scout.rank(result_rows, scenarios, budget=3)
    second = scout.rank(result_rows, scenarios, budget=3)

    assert len(first) == 3
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]
    assert [item.selected_rank for item in first] == [1, 2, 3]


def test_heldout_audit_rejects_oracle_and_case_specific_access() -> None:
    safe = audit_candidate_source(inspect.getsource(_safe_candidate))
    cheating = audit_candidate_source(inspect.getsource(_cheating_candidate))
    getattr_case = audit_candidate_source(
        'def candidate(value):\n    return getattr(value, "case_id")\n'
    )

    assert safe["passed"] is True
    assert cheating["passed"] is False
    assert getattr_case["passed"] is False


def test_heldout_evaluator_keeps_oracle_outside_candidate_and_preserves_history() -> None:
    scenario = build_scenario_matrix("fixtures")[0]
    report = IsolatedHeldoutEvaluator().evaluate(
        [scenario],
        _safe_candidate,
        context_budget_tokens=128,
    )

    assert report["status"] == "PASS"
    assert report["candidate_has_oracle_input"] is False
    assert report["oracle_isolation"] is True
    assert report["raw_history_hash_before"] == report["raw_history_hash_after"]
    assert report["cases"][0]["candidate"]["selected_event_ids"]


def test_d6_suite_structural_gates_pass_without_e2_claim(tmp_path: Path) -> None:
    report = run_d6_suite(
        "fixtures",
        RuntimeConfig(),
        d5_report="docs/EVAL_RESULTS_V02.json",
        output_json=tmp_path / "FAILURE_REGISTER.json",
        output_markdown=tmp_path / "FAILURE_REGISTER.md",
    )

    assert report["schema_version"] == "failure-register.v0.2.d6"
    assert report["heldout"]["case_count"] > 0
    assert all(report["gates"].values())
    assert report["interpretation_boundary"]["e2"] == "NOT OBSERVED / OPEN GAP"
