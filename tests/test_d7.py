from __future__ import annotations

import json
from pathlib import Path

import pytest

if not Path("docs/EVAL_RESULTS_V02.json").is_file():
    pytest.skip(
        "the Week02 evaluation report is not distributed in the public snapshot",
        allow_module_level=True,
    )

from team_soul.config import RuntimeConfig
from team_soul.eval_d7 import run_d7_suite, run_scaffold_ablation
from team_soul.provider_boundary import inspect_second_provider_boundary
from team_soul.reasoning import ReasoningTrace, build_reasoning_trace, private_cot_fields


def _d5_report() -> dict:
    return json.loads(Path("docs/EVAL_RESULTS_V02.json").read_text(encoding="utf-8"))


def test_reasoning_trace_contains_only_declared_auditable_fields() -> None:
    result = _d5_report()["cases"][0]
    trace = build_reasoning_trace(result, "SOUL")
    payload = trace.model_dump(mode="json")
    required = {
        "trace_id",
        "problem_ids",
        "claims_considered",
        "evidence_used",
        "conflicting_evidence",
        "transfer_candidates",
        "rejected_alternatives",
        "closure_preconditions",
        "unresolved_items",
        "proposed_action",
        "verification",
        "token_cost",
        "latency_ms",
        "trace_hash",
    }

    assert set(payload) == required
    assert private_cot_fields() == ()
    assert ReasoningTrace.model_validate(payload).trace_hash == trace.trace_hash


def test_scaffold_ablation_covers_direct_and_structured_trace() -> None:
    report = run_scaffold_ablation(_d5_report()["cases"][:2])

    assert report["modes"] == ["DIRECT", "STRUCTURED_TRACE"]
    assert report["row_count"] == 12
    assert report["summary"]["DIRECT"]["trace_completion_rate"] == 0.0
    assert report["summary"]["STRUCTURED_TRACE"]["trace_completion_rate"] == 1.0
    assert all(row["trace_hash"] for row in report["rows"] if row["mode"] == "STRUCTURED_TRACE")
    assert report["utility_evidence"]["status"] == "NOT OBSERVED / OPEN GAP"


def test_second_provider_boundary_is_adapter_only_without_network() -> None:
    report = inspect_second_provider_boundary(RuntimeConfig())

    assert report["second"]["adapter_available"] is True
    assert report["second"]["adapter_class"] == "OpenAICompatibleProvider"
    assert report["network_called"] is False
    assert report["execution_status"] == "NOT OBSERVED / OPEN GAP"


def test_d7_partial_report_keeps_provider_and_e2_boundaries(tmp_path: Path) -> None:
    report = run_d7_suite(
        "fixtures",
        RuntimeConfig(),
        d5_report="docs/EVAL_RESULTS_V02.json",
        d6_report="docs/FAILURE_REGISTER.json",
        output_json=tmp_path / "MULTI_MODEL_PARTIAL_REPORT.json",
        output_markdown=tmp_path / "EVAL_REPORT_D7.md",
        reasoning_output=tmp_path / "REASONING_ABLATION_V02.json",
    )

    assert report["schema_version"] == "eval.v0.2.d7.partial"
    assert report["gates"]["d6_dependency"] is True
    assert report["gates"]["base_rag_soul_present"] is True
    assert report["gates"]["required_ablations_present"] is True
    assert report["gates"]["reasoning_trace_schema"] is True
    assert report["gates"]["no_private_cot_persistence"] is True
    assert report["gates"]["reasoning_scaffold_utility"] == "NOT OBSERVED / OPEN GAP"
    assert report["gates"]["second_provider_execution"] == "NOT OBSERVED / OPEN GAP"
    assert report["interpretation_boundary"]["e2"] == "NOT OBSERVED / OPEN GAP"
