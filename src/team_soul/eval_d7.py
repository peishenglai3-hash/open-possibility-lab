from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .hashing import sha256_json
from .provider_boundary import inspect_second_provider_boundary
from .reasoning import (
    ReasoningMode,
    build_reasoning_trace,
    private_cot_fields,
)


REQUIRED_ARMS = ("BASE", "RAG", "SOUL")
REQUIRED_ABLATIONS = (
    "no-dissent",
    "no-temporal",
    "no-transfer",
    "no-closure",
    "no-problem",
)


def run_d7_suite(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    d5_report: str | Path = "docs/EVAL_RESULTS_V02.json",
    d6_report: str | Path = "docs/FAILURE_REGISTER.json",
    output_json: str | Path = "docs/MULTI_MODEL_PARTIAL_REPORT.json",
    output_markdown: str | Path = "docs/EVAL_REPORT_D7.md",
    reasoning_output: str | Path = "docs/REASONING_ABLATION_V02.json",
) -> dict[str, Any]:
    """Run D7's auditable scaffold/provider boundary over frozen D5/D6 evidence."""

    fixture_path = Path(fixture_dir)
    if not fixture_path.is_dir():
        raise FileNotFoundError(f"fixture directory not found: {fixture_path}")
    # D7 consumes the frozen D5 matrix; no new corpus is admitted here.
    d5 = json.loads(Path(d5_report).read_text(encoding="utf-8"))
    d6 = json.loads(Path(d6_report).read_text(encoding="utf-8"))
    cases = d5.get("cases", [])
    scaffold = run_scaffold_ablation(cases)
    provider = inspect_second_provider_boundary(config)
    source_checks = _source_checks(d5, d6)
    trace_checks = _trace_checks(scaffold)
    report = {
        "schema_version": "eval.v0.2.d7.partial",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_d5_report": str(d5_report),
        "source_d5_report_hash": sha256_json(d5),
        "source_d6_report": str(d6_report),
        "source_d6_report_hash": sha256_json(d6),
        "requested_arms": list(REQUIRED_ARMS),
        "executed_arms": sorted(
            {arm for case in cases for arm in case.get("arms", {})}
        ),
        "source_checks": source_checks,
        "reasoning_scaffold": scaffold,
        "provider_boundary": provider,
        "gates": {
            "d6_dependency": source_checks["d6_gates_all_pass"],
            "base_rag_soul_present": source_checks["base_rag_soul_present"],
            "required_ablations_present": source_checks["required_ablations_present"],
            "reasoning_trace_schema": trace_checks["schema_pass"],
            "direct_vs_structured_modes": trace_checks["both_modes_present"],
            "no_private_cot_persistence": trace_checks["no_private_cot_fields"],
            "reasoning_scaffold_utility": scaffold["utility_evidence"]["status"],
            "second_provider_adapter": provider["second"]["adapter_available"],
            "second_provider_execution": provider["execution_status"],
            "e2_historical_replay": "NOT OBSERVED / OPEN GAP",
        },
        "interpretation_boundary": {
            "status": "STRUCTURAL_PARTIAL_PASS",
            "reasoning": "external structured scaffold comparison only; no private CoT claim",
            "reasoning_utility": scaffold["utility_evidence"]["status"],
            "provider": provider["execution_status"],
            "e2": "NOT OBSERVED / OPEN GAP",
            "e4": "NOT OBSERVED / OPEN GAP",
            "real_world_utility": "NOT OBSERVED / OPEN GAP",
        },
    }
    _write_json(output_json, report)
    _write_json(reasoning_output, scaffold)
    Path(output_markdown).parent.mkdir(parents=True, exist_ok=True)
    Path(output_markdown).write_text(render_markdown(report), encoding="utf-8")
    return report


def run_scaffold_ablation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        for arm in REQUIRED_ARMS:
            arm_result = case.get("arms", {}).get(arm)
            if arm_result is None:
                continue
            rows.append(_direct_row(case, arm, arm_result))
            started = time.perf_counter_ns()
            trace = build_reasoning_trace(case, arm)
            elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 6)
            trace_payload = trace.model_dump(mode="json")
            rows.append(
                {
                    "scenario_id": case.get("scenario", {}).get("scenario_id", case.get("case_id", "")),
                    "case_id": case.get("case_id", ""),
                    "arm": arm,
                    "mode": ReasoningMode.STRUCTURED_TRACE.value,
                    "trace": trace_payload,
                    "trace_hash": trace.trace_hash,
                    "private_cot_fields": list(private_cot_fields()),
                    "metrics": arm_result.get("metrics", {}),
                    "cost": {
                        "input_tokens": arm_result.get("context", {}).get("input_tokens", 0),
                        "output_tokens": arm_result.get("response", {}).get("output_tokens", 0),
                        "scaffold_tokens": trace.token_cost or 0,
                        "scaffold_latency_ms": elapsed_ms,
                        "measurement": "local structured scaffold construction",
                    },
                }
            )
    return {
        "schema_version": "reasoning-ablation.v0.2.d7",
        "modes": [ReasoningMode.DIRECT.value, ReasoningMode.STRUCTURED_TRACE.value],
        "case_count": len(cases),
        "row_count": len(rows),
        "rows": rows,
        "summary": _summarize_rows(rows),
        "utility_evidence": {
            "status": "NOT OBSERVED / OPEN GAP",
            "reason": (
                "D7 reuses frozen D5 outputs; it does not invoke a second provider or "
                "generate answers from the structured trace, so paired outcome deltas "
                "cannot establish scaffold utility."
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.2 D7 Partial Multi-Model Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Status: `{report['interpretation_boundary']['status']}`",
        "",
        "## Gate summary",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for name, value in report["gates"].items():
        lines.append(f"| {name} | {value} |")
    scaffold = report["reasoning_scaffold"]
    lines.extend(
        [
            "",
            "## Reasoning scaffold ablation",
            "",
            f"- Cases: `{scaffold['case_count']}`; rows: `{scaffold['row_count']}`",
            "- DIRECT stores no private reasoning trace.",
            "- STRUCTURED_TRACE stores only the declared auditable fields.",
            f"- Utility evidence: `{scaffold['utility_evidence']['status']}` — {scaffold['utility_evidence']['reason']}",
            "",
            "| Mode | Rows | Trace completion | Mean scaffold tokens | Mean scaffold ms |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for mode, summary in scaffold["summary"].items():
        lines.append(
            f"| {mode} | {summary['row_count']} | {summary['trace_completion_rate']} | "
            f"{summary['mean_scaffold_tokens']} | {summary['mean_scaffold_latency_ms']} |"
        )
    provider = report["provider_boundary"]
    lines.extend(
        [
            "",
            "## Provider boundary",
            "",
            f"- Primary: `{provider['primary']['kind']}` / `{provider['primary']['model']}`",
            f"- Second adapter: `{provider['second']['kind']}` / `{provider['second']['adapter_class']}`",
            f"- External execution: `{provider['execution_status']}`",
            "- No network call was made by this D7 run.",
            "",
            "## Interpretation boundary",
            "",
            "This is a structural partial report over frozen D5/D6 evidence. "
            "It is not external second-provider evidence, E2 historical evidence, "
            "E4 live-pilot evidence, or a real-world utility claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _direct_row(case: dict[str, Any], arm: str, arm_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": case.get("scenario", {}).get("scenario_id", case.get("case_id", "")),
        "case_id": case.get("case_id", ""),
        "arm": arm,
        "mode": ReasoningMode.DIRECT.value,
        "trace": None,
        "trace_hash": None,
        "private_cot_fields": [],
        "metrics": arm_result.get("metrics", {}),
        "cost": {
            "input_tokens": arm_result.get("context", {}).get("input_tokens", 0),
            "output_tokens": arm_result.get("response", {}).get("output_tokens", 0),
            "scaffold_tokens": 0,
            "scaffold_latency_ms": 0.0,
            "measurement": "direct provider-boundary output; scaffold cost not added",
        },
    }


def _source_checks(d5: dict[str, Any], d6: dict[str, Any]) -> dict[str, Any]:
    cases = d5.get("cases", [])
    observed_arms = {arm for case in cases for arm in case.get("arms", {})}
    ablations = d5.get("ablations", {})
    modes = set(ablations.get("modes", []))
    d6_gates = d6.get("gates", {})
    return {
        "case_count": len(cases),
        "base_rag_soul_present": set(REQUIRED_ARMS).issubset(observed_arms),
        "observed_arms": sorted(observed_arms),
        "required_ablations_present": set(REQUIRED_ABLATIONS).issubset(modes)
        and int(ablations.get("case_count", 0)) > 0,
        "ablation_modes": sorted(modes),
        "d6_gates_all_pass": bool(d6_gates) and all(value is True for value in d6_gates.values()),
    }


def _trace_checks(scaffold: dict[str, Any]) -> dict[str, Any]:
    rows = scaffold.get("rows", [])
    structured = [row for row in rows if row.get("mode") == ReasoningMode.STRUCTURED_TRACE.value]
    modes = {row.get("mode") for row in rows}
    forbidden = set(private_cot_fields())
    schema_pass = bool(structured) and all(
        row.get("trace")
        and row.get("trace_hash") == row["trace"].get("trace_hash")
        and not set(row.get("private_cot_fields", [])).intersection(forbidden)
        and all(
            key in row["trace"]
            for key in (
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
            )
        )
        for row in structured
    )
    return {
        "schema_pass": schema_pass,
        "both_modes_present": modes == {ReasoningMode.DIRECT.value, ReasoningMode.STRUCTURED_TRACE.value},
        "no_private_cot_fields": not forbidden,
        "structured_row_count": len(structured),
    }


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in (ReasoningMode.DIRECT.value, ReasoningMode.STRUCTURED_TRACE.value):
        selected = [row for row in rows if row.get("mode") == mode]
        if not selected:
            continue
        summary[mode] = {
            "row_count": len(selected),
            "trace_completion_rate": round(
                sum(1 for row in selected if row.get("trace") is not None) / len(selected), 6
            ),
            "mean_scaffold_tokens": round(
                sum(float(row["cost"].get("scaffold_tokens", 0)) for row in selected)
                / len(selected),
                6,
            ),
            "mean_scaffold_latency_ms": round(
                sum(float(row["cost"].get("scaffold_latency_ms", 0.0)) for row in selected)
                / len(selected),
                6,
            ),
            "mean_provenance_coverage": _mean_metric(selected, "provenance_coverage"),
            "mean_dissent_preservation_rate": _mean_metric(
                selected, "dissent_preservation_rate"
            ),
        }
    return summary


def _mean_metric(rows: list[dict[str, Any]], name: str) -> float | None:
    values = [float(row.get("metrics", {}).get(name, 0.0)) for row in rows]
    return round(sum(values) / len(values), 6) if values else None


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = ["run_d7_suite", "run_scaffold_ablation", "render_markdown"]
