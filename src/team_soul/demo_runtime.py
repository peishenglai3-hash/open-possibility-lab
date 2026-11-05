from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .eval import ARMS, run_case
from .hashing import sha256_json
from .kernel.policies import POLICY_SEAM_CATALOG
from .scenario import AblationKind, EvidenceLevel, ScenarioCase, ScenarioEnvironment, ScenarioSpec, ablate_case
from .contracts import EvalCase


def run_demo_inspection(
    case: EvalCase,
    config: RuntimeConfig,
    *,
    query: str | None = None,
    failure_report_path: str | Path | None = "docs/FAILURE_REGISTER.json",
) -> dict[str, Any]:
    """Shared CLI/Streamlit path for arms, trace, policy, failure, and ablation views."""

    effective_case = case.model_copy(update={"query": query if query is not None else case.query})
    baseline = run_case(effective_case, config, arms=ARMS)
    base_scenario = ScenarioCase(
        spec=ScenarioSpec(
            scenario_id=f"SYN-{case.case_id}",
            source_case_id=case.case_id,
            mechanism="selected_fixture_demo",
            environment=ScenarioEnvironment.SYNTHETIC,
            evidence_level=EvidenceLevel.E1,
            tags=("demo", "fixture"),
        ),
        case=effective_case,
    )
    ablation_rows: dict[str, Any] = {}
    for kind in AblationKind:
        altered = ablate_case(base_scenario, kind).case
        ablated_result = run_case(altered, config, arms=ARMS)
        arm_metrics = {
            arm: payload["metrics"] for arm, payload in ablated_result["arms"].items()
        }
        soul_metrics = ablated_result["arms"].get("SOUL", {}).get("metrics", {})
        original_soul_metrics = baseline["arms"].get("SOUL", {}).get("metrics", {})
        metric_deltas = {
            metric: round(value - original_soul_metrics[metric], 6)
            for metric, value in soul_metrics.items()
            if metric in original_soul_metrics
        }
        ablation_rows[kind.value] = {
            "arm_metrics": arm_metrics,
            "soul_metric_delta_vs_unablated": metric_deltas,
            "projection_hash": ablated_result["projection_hash"],
        }

    arm_rows = {}
    for arm, result in baseline["arms"].items():
        arm_rows[arm] = {
            "metrics": result["metrics"],
            "selected_event_ids": result["context"]["selected_event_ids"],
            "input_tokens": result["context"]["input_tokens"],
            "context_hash": result["context"]["context_hash"],
            "response": result["response"]["answer"],
            "cited_event_ids": result["response"]["cited_event_ids"],
            "source_trace": result["retrieval"]["selected"],
        }

    failure_scout = _failure_candidates(failure_report_path, case.case_id)
    policy_differences = {
        "reference_seams": list(POLICY_SEAM_CATALOG),
        "persisted_policy_events_in_fixture_sandbox": 0,
        "arm_selected_event_ids": {
            arm: payload["selected_event_ids"] for arm, payload in arm_rows.items()
        },
        "interpretation": (
            "Selection and metric differences are observed for this run; the isolated fixture "
            "sandbox does not claim any persisted user policy is active."
        ),
    }
    reproducible_core = {
        "case_id": case.case_id,
        "query": effective_case.query,
        "config": {
            "context_budget_tokens": config.context_budget_tokens,
            "rrf_k": config.rrf_k,
            "candidate_top_n": config.candidate_top_n,
            "provider_kind": config.provider_kind,
            "provider_model": config.provider_model,
            "temperature": config.temperature,
        },
        "arms": {
            arm: {
                "metrics": item["metrics"],
                "selected_event_ids": item["selected_event_ids"],
                "context_hash": item["context_hash"],
            }
            for arm, item in arm_rows.items()
        },
        "ablations": {
            kind: {
                "arm_metrics": row["arm_metrics"],
                "soul_metric_delta_vs_unablated": row["soul_metric_delta_vs_unablated"],
            }
            for kind, row in ablation_rows.items()
        },
    }
    return {
        "schema_version": "demo-inspection.v0.2.d9",
        "evidence_level": "E1",
        "mode": "FIXTURE_EXPERIMENT_ONLY",
        "experiment_hash": sha256_json(reproducible_core),
        "case_id": case.case_id,
        "query": effective_case.query,
        "projection_hash": baseline["projection_hash"],
        "projection_stable": baseline["projection_stable"],
        "arms": arm_rows,
        "policy_differences": policy_differences,
        "failure_scout": failure_scout,
        "ablations": ablation_rows,
        "provider_dependency": {
            "kind": config.provider_kind,
            "model": config.provider_model,
            "external_provider_execution": "NOT OBSERVED / OPEN GAP"
            if config.provider_kind == "mock"
            else "CONFIGURED_PROVIDER_USED",
        },
        "interpretation_boundary": (
            "A single synthetic fixture inspection is a reproducible interface/demo check, "
            "not E2/E4 or a real-world benefit claim."
        ),
    }


def _failure_candidates(
    report_path: str | Path | None, case_id: str
) -> dict[str, Any]:
    if report_path is None or not Path(report_path).is_file():
        return {"status": "NOT OBSERVED / OPEN GAP", "candidates": []}
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    candidates = report.get("failure_scout", {}).get("candidates", [])
    selected = [
        candidate
        for candidate in candidates
        if case_id in str(candidate.get("scenario_id", candidate.get("scenario", "")))
    ]
    return {
        "status": report.get("failure_scout", {}).get("status", "STRUCTURAL_CANDIDATES"),
        "source_report": str(report_path),
        "candidate_count_for_case": len(selected),
        "candidates": selected,
        "interpretation": "FailureScout candidates are experiment-queue items, not verified real failures.",
    }


__all__ = ["run_demo_inspection"]
