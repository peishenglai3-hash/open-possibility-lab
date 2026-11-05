from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .demo_runtime import run_demo_inspection
from .eval_d6 import run_d6_suite
from .eval_d7 import run_d7_suite
from .eval_promotion import run_promotion_dry_run
from .eval_v02 import run_v02_suite
from .hashing import sha256_json, sha256_text
from .ingest import load_fixture
from .kernel.policies import policy_catalog_markdown
from .pilot import run_pilot_d8_suite


def run_d9_bundle(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    allow_configured_provider: bool = False,
) -> dict[str, Any]:
    """Rerun the scheduled V0.2 engineering bundle and record its actual gates."""

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(
            f"D9 requires the frozen Python 3.12 runtime, got {sys.version_info.major}.{sys.version_info.minor}"
        )
    if config.provider_kind != "mock" and not allow_configured_provider:
        raise PermissionError(
            "configured non-mock provider run requires explicit authorization flag"
        )
    root = Path.cwd()
    docs = root / "docs"
    d5_path = docs / "EVAL_RESULTS_V02.json"
    d6_path = docs / "FAILURE_REGISTER.json"
    d7_path = docs / "MULTI_MODEL_PARTIAL_REPORT.json"
    d8_path = docs / "PILOT_NOTES_D8.json"
    promotion_path = docs / "PROMOTION_DRY_RUN_D9.json"
    demo_path = docs / "DEMO_INSPECTION_V02.json"

    d5 = run_v02_suite(
        fixture_dir,
        config,
        output_json=d5_path,
        output_markdown=config.data_dir / "EVAL_REPORT_D5_INTERNAL.md",
        output_manifest=docs / "SCENARIO_MATRIX_V02.json",
    )
    d6 = run_d6_suite(
        fixture_dir,
        config,
        d5_report=d5_path,
        output_json=d6_path,
        output_markdown=docs / "FAILURE_REGISTER.md",
    )
    d7 = run_d7_suite(
        fixture_dir,
        config,
        d5_report=d5_path,
        d6_report=d6_path,
        output_json=d7_path,
        output_markdown=docs / "EVAL_REPORT_D7.md",
        reasoning_output=docs / "REASONING_ABLATION_V02.json",
    )
    d8 = run_pilot_d8_suite(
        fixture_dir,
        config,
        output_json=d8_path,
        output_markdown=docs / "PILOT_NOTES.md",
        output_steering_json=docs / "STEERING_AUDIT_D8.json",
    )
    promotion = run_promotion_dry_run(d5_path, d6_path, config)
    _write_json(promotion_path, promotion)
    demo = run_demo_inspection(
        load_fixture(Path(fixture_dir) / "C01_historical_revision.json"),
        config,
        failure_report_path=docs / "FAILURE_REGISTER.json",
    )
    _write_json(demo_path, demo)
    (docs / "POLICY_REGISTRY.md").write_text(policy_catalog_markdown(), encoding="utf-8")

    test_run = _run_full_tests(root)
    report = build_final_report(d5, d6, d7, d8, promotion, test_run)
    (docs / "EVAL_REPORT_V02.md").write_text(render_final_report(report), encoding="utf-8")
    (docs / "D9_GATE.md").write_text(render_d9_gate(report), encoding="utf-8")
    (docs / "V03-BOUNDARY.md").write_text(render_v03_boundary(report), encoding="utf-8")
    manifest = {
        "schema_version": "d9-run-manifest.v0.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "provider_kind": config.provider_kind,
        "provider_model": config.provider_model,
        "executed_arms": d5.get("executed_arms", []),
        "scenario_manifest_hash": d5.get("scenario_manifest_hash"),
        "full_test_result": test_run,
        "artifact_hashes": {
            path.name: _file_hash(path)
            for path in (
                d5_path,
                d6_path,
                d7_path,
                d8_path,
                promotion_path,
                demo_path,
                docs / "REASONING_ABLATION_V02.json",
                docs / "SCENARIO_MATRIX_V02.json",
            )
        },
        "overall_gate": report["overall_gate"],
        "external_provider_execution": d7.get("provider_boundary", {}).get(
            "execution_status", "NOT OBSERVED / OPEN GAP"
        ),
    }
    _write_json(docs / "D9_RUN_MANIFEST.json", manifest)
    return {"report": report, "run_manifest": manifest}


def build_final_report(
    d5: dict[str, Any],
    d6: dict[str, Any],
    d7: dict[str, Any],
    d8: dict[str, Any],
    promotion: dict[str, Any],
    test_run: dict[str, Any],
) -> dict[str, Any]:
    matrix = d5.get("scenario_matrix", {})
    counts = matrix.get("evidence_counts", {})
    scorecard = d5.get("scorecard", {}).get("by_arm", {}).get("SOUL", {})
    d6_gates = d6.get("gates", {})
    d7_gates = d7.get("gates", {})
    d7_provider = d7.get("provider_boundary", {})
    test_passed = test_run.get("return_code") == 0 and test_run.get("passed") is not None
    transfer = _transfer_precision_recall(d5)
    contributions = _mechanism_contributions(d5)
    providers = _observed_core_provider_families(d5)
    all_arm_metrics = [
        arm.get("metrics", {})
        for case in d5.get("cases", [])
        for arm in case.get("arms", {}).values()
    ]
    transfer_metric_rows = [row for row in all_arm_metrics if "negative_transfer_rejection" in row]
    team_scope_rows = [row for row in all_arm_metrics if "team_level_overreach" in row]

    gates: dict[str, dict[str, Any]] = {}
    gates["scenario_matrix_24_to_32"] = _gate(
        "PASS" if 24 <= matrix.get("declared_case_count", 0) <= 32 else "FAIL",
        f"{matrix.get('declared_case_count', 0)} frozen scenarios",
        "24-32 scenarios",
    )
    gates["historical_e2_min_8"] = _gate(
        "PASS" if counts.get("E2", 0) >= 8 else "NOT OBSERVED / OPEN GAP",
        f"{counts.get('E2', 0)} qualified historical cases",
        "at least 8 human-approved E2 cases; synthetic data cannot substitute",
    )
    gates["perturbation_e3_min_8"] = _gate(
        "PASS" if counts.get("E3", 0) >= 8 else "FAIL",
        f"{counts.get('E3', 0)} adversarial perturbation cases",
        "at least 8 E3 cases",
    )
    gates["two_provider_families_on_core_arms"] = _gate(
        "PASS" if len(providers) >= 2 else "NOT OBSERVED / OPEN GAP",
        f"{len(providers)} observed core provider/model identities: {', '.join(providers) or 'none'}",
        "at least two provider/model families execute BASE/RAG/SOUL on the declared corpus",
    )
    hard_checks = {
        "d5_matrix_and_replay_gates": all(
            d5.get("gates", {}).get(key) is True
            for key in (
                "scenario_target_24_to_32",
                "scenario_execution_complete",
                "perturbation_runner",
                "ablation_runner",
                "projection_rebuild_stable",
                "no_unobserved_e2_claim",
            )
        ),
        "d5_hard_gate_metrics": bool(all_arm_metrics and transfer_metric_rows and team_scope_rows)
        and all(row["negative_transfer_rejection"] == 1.0 for row in transfer_metric_rows)
        and all(row["team_level_overreach"] == 0.0 for row in team_scope_rows)
        and all(row.get("false_closure_rate", 0.0) <= 0.10 for row in all_arm_metrics),
        "d6_isolation_and_overfit_gates": all(
            value is True for value in d6_gates.values() if isinstance(value, bool)
        ),
        "d7_trace_safety_gates": all(
            d7_gates.get(key) is True
            for key in (
                "reasoning_trace_schema",
                "direct_vs_structured_modes",
                "no_private_cot_persistence",
            )
        ),
        "d8_suggestion_only": d8.get("all_suggestions_proposals") is True,
        "d8_raw_history_unchanged": d8.get("raw_history_unchanged") is True,
        "full_test_suite": test_passed,
    }
    gates["hard_invariants_100_percent"] = _gate(
        "PASS" if all(hard_checks.values()) else "FAIL",
        json.dumps(hard_checks, sort_keys=True),
        "all exercised invariant and anti-cheating checks pass",
    )

    dissent = _number(scorecard.get("dissent_preservation_rate"))
    gates["dissent_preservation_ge_90_percent"] = _threshold_gate(
        dissent, ">=0.90", lambda value: value >= 0.90,
        "SOUL arm; structural E1/E3 corpus",
    )
    denominators = d5.get("metric_denominators", {})
    false_closure = _number(
        denominators.get("false_closure", {}).get("rate")
        if isinstance(denominators.get("false_closure"), dict)
        else scorecard.get("false_closure_rate")
    )
    gates["false_closure_le_10_percent"] = _threshold_gate(
        false_closure, "<=0.10", lambda value: value <= 0.10,
        "SOUL arm; false-no-closure is reported separately",
    )
    false_no_closure = _number(
        denominators.get("false_no_closure", {}).get("rate")
        if isinstance(denominators.get("false_no_closure"), dict)
        else scorecard.get("false_no_closure_rate")
    )
    gates["false_no_closure_reported"] = _gate(
        "PASS" if false_no_closure is not None else "NOT OBSERVED / OPEN GAP",
        str(false_no_closure) if false_no_closure is not None else "metric unavailable",
        "report the measured rate; do not reward refusal alone",
    )
    provenance = _number(scorecard.get("provenance_coverage"))
    gates["provenance_coverage_ge_95_percent"] = _threshold_gate(
        provenance, ">=0.95", lambda value: value >= 0.95,
        "SOUL arm; measured on the current declared structural corpus",
    )
    gates["unsupported_references_zero"] = unsupported_references_gate(all_arm_metrics)
    unsupported = _number(scorecard.get("unsupported_claim_rate"))
    gates["unsupported_critical_claims_zero"] = _gate(
        "NOT OBSERVED / OPEN GAP",
        f"structural unsupported-citation proxy={unsupported}; manual critical-claim review absent",
        "zero unsupported critical claims under independent claim review",
    )
    gates["transfer_precision_recall_reported"] = _gate(
        "PASS"
        if transfer["status"] == "OBSERVED"
        and transfer["precision"] is not None
        and transfer["recall"] is not None
        else "NOT OBSERVED / OPEN GAP",
        json.dumps(transfer, sort_keys=True),
        "report precision and recall from labeled transfer cases",
    )
    gates["independent_mechanism_contribution"] = _gate(
        "PASS" if any(item.get("contributes") for item in contributions.values()) else "FAIL",
        json.dumps(contributions, sort_keys=True),
        "at least one named mechanism must improve its frozen mechanism-specific metric",
    )
    gates["reasoning_utility_causal"] = _gate(
        "PASS" if d7_gates.get("reasoning_scaffold_utility") is True else "NOT OBSERVED / OPEN GAP",
        str(d7_gates.get("reasoning_scaffold_utility")),
        "paired answer outcomes generated with/without structured trace",
    )
    gates["external_second_provider"] = _gate(
        "PASS" if d7_provider.get("execution_status") == "PASS" else "NOT OBSERVED / OPEN GAP",
        str(d7_provider.get("execution_status", "NOT OBSERVED / OPEN GAP")),
        "actual second-provider responses, not adapter construction smoke",
    )
    gates["e4_live_pilot_and_real_failure_repair"] = _gate(
        "PASS"
        if d8.get("e4_live_pilot") == "PASS" and d8.get("real_failure_repair", {}).get("status") == "PASS"
        else "NOT OBSERVED / OPEN GAP",
        f"pilot={d8.get('e4_live_pilot')}; repair={d8.get('real_failure_repair', {}).get('status')}",
        "human-reviewed live suggestions and one real failure repaired without oracle adjustment",
    )
    gates["promotion_dry_run"] = _gate(
        "PASS" if promotion.get("promotion_decision", {}).get("auto_promote") is False and promotion.get("raw_history_unchanged") is True else "FAIL",
        f"decision={promotion.get('promotion_decision', {}).get('decision')}; auto_promote={promotion.get('auto_promote')}; raw_history_unchanged={promotion.get('raw_history_unchanged')}",
        "candidate sandbox/held-out/promotion dry-run exists; no auto-promotion or raw mutation",
    )
    gates["candidate_lifecycle_gate"] = _gate(
        "PASS" if test_passed else "FAIL",
        "DRAFT/CANDIDATE -> verified SANDBOXED -> VALIDATED -> human-approved -> ACTIVE; direct ACTIVE registration and sandbox skipping are rejected",
        "append-only registry enforces sandbox evidence and human confirmation",
    )
    gates["streamlit_apptest"] = _gate(
        "PASS" if test_passed else "FAIL",
        "AppTest loads the page, clicks run, and verifies all four experiment tabs without exceptions",
        "CLI/Streamlit shared runtime path",
    )
    gates["streamlit_browser_visual_qa"] = _gate(
        "NOT OBSERVED / OPEN GAP",
        "Chrome DevTools MCP is not configured; in-process Streamlit AppTest was used",
        "browser screenshot, console, accessibility tree, and network inspection",
    )

    unresolved = [
        name for name, item in gates.items()
        if item["status"] not in {"PASS"}
    ]
    overall = "GO" if not unresolved else "HOLD"
    return {
        "schema_version": "eval-report.v0.2.final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_gate": overall,
        "gate_summary": gates,
        "unresolved_or_failed_gates": unresolved,
        "observed_successes": {
            "scenario_count": matrix.get("declared_case_count", 0),
            "evidence_counts": counts,
            "executed_arms": d5.get("executed_arms", []),
            "ablation_modes": d5.get("ablations", {}).get("modes", []),
            "d6_gates": d6_gates,
            "d7_structural_gates": {
                key: value for key, value in d7_gates.items()
                if isinstance(value, bool)
            },
            "d8_engineering": {
                "all_suggestions_proposals": d8.get("all_suggestions_proposals"),
                "raw_history_unchanged": d8.get("raw_history_unchanged"),
            },
            "full_test_result": test_run,
        },
        "measured_failures": {
            "scorecard_soul": scorecard,
            "mechanism_contributions": contributions,
            "promotion_decision": promotion.get("promotion_decision", {}),
        },
        "accepted_limits": [
            "E1 synthetic and E3 perturbation results establish structural behavior only.",
            "No E2 historical corpus or E4 human live-pilot record was supplied.",
            "D6 held-out set is only six synthetic/perturbation cases; power is low.",
            "No second provider was configured or called; adapter smoke is not a provider run.",
            "D7 reuses frozen answers, so causal reasoning-scaffold utility is unobserved.",
            "Unsupported citation rate is a structural proxy, not independent critical-claim review.",
            "No real-failure repair was observed; fixtures/oracles were not altered to manufacture one.",
            "Chrome DevTools MCP is not configured; Streamlit AppTest passed, but browser visual/console/accessibility QA remains unobserved.",
        ],
        "provider_dependency": {
            "observed_core_identities": providers,
            "primary": d7_provider.get("primary", {}),
            "second_adapter": d7_provider.get("second", {}),
            "second_provider_execution": d7_provider.get("execution_status", "NOT OBSERVED / OPEN GAP"),
        },
        "exit_condition": {
            "status": overall,
            "required_action": (
                "Do not describe V0.2 as empirically released. Keep this as an engineering candidate "
                "until open evidence and measured threshold failures are resolved."
                if overall != "GO" else "Engineering and empirical exit conditions passed."
            ),
        },
        "projection_and_replay_hashes": {
            "scenario_manifest_hash": d5.get("scenario_manifest_hash"),
            "d6_split_hash": d6.get("split", {}).get("split_hash"),
            "d6_raw_history_hash_before": d6.get("heldout", {}).get("raw_history_hash_before"),
            "d6_raw_history_hash_after": d6.get("heldout", {}).get("raw_history_hash_after"),
            "promotion_raw_history_hash_before": promotion.get("raw_history_hash_before"),
            "promotion_raw_history_hash_after": promotion.get("raw_history_hash_after"),
        },
        "test_run": test_run,
    }


def render_final_report(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.2 Final Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Overall gate: **{report['overall_gate']}**",
        "",
        "## Exit-condition table",
        "",
        "| Exit condition | Status | Observed value | Requirement / boundary |",
        "|---|---|---|---|",
    ]
    for name, item in report["gate_summary"].items():
        lines.append(
            f"| {name} | {item['status']} | {item['observed']} | {item['requirement']} |"
        )
    lines.extend(["", "## Successful structural results", ""])
    successes = report["observed_successes"]
    lines.append(
        f"- Scenario matrix: `{successes['scenario_count']}` cases; evidence counts `{successes['evidence_counts']}`."
    )
    lines.append(
        f"- Arms: `{', '.join(successes['executed_arms'])}`; ablations: `{', '.join(successes['ablation_modes'])}`."
    )
    lines.append(f"- Full test run: `{successes['full_test_result'].get('summary')}`.")
    lines.append("- D6 structural gates: `" + json.dumps(successes["d6_gates"], sort_keys=True) + "`.")
    lines.extend(["", "## Measured failures and unresolved claims", ""])
    lines.append(
        "- Current SOUL scorecard: `" + json.dumps(report["measured_failures"]["scorecard_soul"], sort_keys=True) + "`."
    )
    lines.append(
        "- Promotion dry-run: `" + str(report["measured_failures"]["promotion_decision"].get("decision"))
        + "`; it does not activate a policy."
    )
    lines.append("- Gates not passed/open: `" + ", ".join(report["unresolved_or_failed_gates"]) + "`.")
    lines.extend(["", "## Accepted limits", ""])
    lines.extend(f"- {item}" for item in report["accepted_limits"])
    lines.extend(
        [
            "",
            "## Provider dependency",
            "",
            f"- Core-run provider/model identities: `{report['provider_dependency']['observed_core_identities']}`.",
            f"- Second provider execution: `{report['provider_dependency']['second_provider_execution']}`.",
            "- The local mock run cannot establish model-family independence or external-provider robustness.",
            "",
            "## Hashes and reproducibility",
            "",
            "```json",
            json.dumps(report["projection_and_replay_hashes"], ensure_ascii=False, indent=2),
            "```",
            "",
            "CLI reproduction is recorded in `docs/D9_GATE.md` and `docs/D9_RUN_MANIFEST.json`.",
            "V0.2 release candidate decision: **HOLD** unless all open gates are resolved with evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def render_d9_gate(report: dict[str, Any]) -> str:
    tests = report["test_run"]
    hashes = report["projection_and_replay_hashes"]
    lines = [
        "# Team Soul V0.2 D9 Gate",
        "",
        f"Date: {report['generated_at']}",
        f"Verdict: **{report['overall_gate']} — engineering candidate complete; empirical release gate not passed**",
        "",
        "## Full rerun",
        "",
        f"- Runtime: `{sys.version.split()[0]}`",
        f"- Tests: `{tests.get('summary')}` (exit `{tests.get('return_code')}`)",
        "- BASE/RAG/SOUL and five named ablations: executed on the frozen 26-case matrix.",
        "- D6 FailureScout/held-out, D7 traces/provider boundary, D8 suggestion-only pilot preview: rerun.",
        "- Candidate sandbox/held-out/promotion dry-run: created; `auto_promote=false`; no policy activated.",
        "- Streamlit and CLI use shared `run_demo_inspection`; CLI output includes trace, policy, FailureScout, and ablation data.",
        "",
        "## Gate details",
        "",
        "| Condition | Status | Evidence |",
        "|---|---|---|",
    ]
    for name, item in report["gate_summary"].items():
        lines.append(f"| {name} | {item['status']} | {item['observed']} |")
    lines.extend(
        [
            "",
            "## Reproducible commands",
            "",
            "Run all commands from the repository root under bundled Python 3.12:",
            "",
            "```powershell",
            "$env:PYTHONPATH = 'src'",
            "python -m pytest -q",
            "team-soul eval v02 --fixture-dir fixtures",
            "team-soul eval d6 --fixture-dir fixtures --d5-report docs/EVAL_RESULTS_V02.json",
            "team-soul eval d7 --fixture-dir fixtures --d5-report docs/EVAL_RESULTS_V02.json --d6-report docs/FAILURE_REGISTER.json",
            "team-soul pilot suite --fixture-dir fixtures",
            "team-soul eval promotion-dry-run --d5-report docs/EVAL_RESULTS_V02.json --d6-report docs/FAILURE_REGISTER.json",
            "team-soul demo-inspect --case C01 --fixture-dir fixtures",
            "team-soul policy-catalog",
            "team-soul eval d9-bundle --fixture-dir fixtures",
            "```",
            "",
            "No historical directory, private corpus, credential, or live provider was included in this run.",
            "",
            "## Replay hashes",
            "",
            "```json",
            json.dumps(hashes, ensure_ascii=False, indent=2),
            "```",
            "",
            "D1-D8 stage checks are in their stage gate reports. The V0.1 baseline tag remains immutable.",
            "This report records an engineering candidate; it is not an empirical release.",
            "",
        ]
    )
    return "\n".join(lines)


def render_v03_boundary(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.3 Boundary",
        "",
        "This file records what V0.2 did not establish. It is a boundary, not authorization to start V0.3.",
        "",
        "## Frozen V0.2 engineering assets",
        "",
        "- Append-only raw history, deterministic projections, temporal policy composition, and hard veto.",
        "- BASE/RAG/SOUL evaluation, five ablations, FailureScout, isolated held-out evaluator, and human-gated promotion dry-run.",
        "- Suggestion-only pilot contracts, SteeringAudit, append-only feedback evidence, shared CLI/Streamlit experiment view.",
        "- Deterministic learnable seams only; no neural training or autonomous policy activation.",
        "",
        "## Advancement blockers",
        "",
        "| Blocker | Current evidence | Required before any broader claim |",
        "|---|---|---|",
        f"| Historical replay | {report['gate_summary']['historical_e2_min_8']['observed']} | At least 8 human-approved E2 cases with provenance and permission boundary |",
        f"| Provider/model dependence | {report['gate_summary']['two_provider_families_on_core_arms']['observed']} | Run BASE/RAG/SOUL under at least two model/provider families on same corpus/config budget |",
        f"| Live pilot | {report['gate_summary']['e4_live_pilot_and_real_failure_repair']['observed']} | Human-reviewed suggestion-only usage plus independently reviewed outcomes |",
        f"| Core metrics | {report['gate_summary']['provenance_coverage_ge_95_percent']['observed']} | Resolve measured threshold failures without changing baseline/oracle/held-out labels |",
        f"| Reasoning utility | {report['gate_summary']['reasoning_utility_causal']['observed']} | Paired fresh outputs with and without structured trace |",
        f"| Unsupported critical claims | {report['gate_summary']['unsupported_critical_claims_zero']['observed']} | Independent review of critical claims and cited evidence |",
        "",
        "## Prohibited inference / action",
        "",
        "- Do not infer a team-level cognitive entity or real productivity gain from E1/E3 fixtures.",
        "- Do not treat a passing adapter smoke as an external provider run.",
        "- Do not train a neural policy, expose private chain-of-thought, or enable `auto_promote`.",
        "- Do not promote a candidate or change the frozen oracle/held-out set to improve a score.",
        "- Do not delete settled V0.1 behavior based solely on this report; any removal requires a separate owner decision and migration evidence.",
        "",
        f"Current V0.2 engineering candidate gate: **{report['overall_gate']}**.",
        "",
    ]
    return "\n".join(lines)


def _mechanism_contributions(d5: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics_by_mode = {
        "no-dissent": ("dissent_preservation_rate", "maximize"),
        "no-temporal": ("temporal_revision_accuracy", "maximize"),
        "no-transfer": ("negative_transfer_rejection", "maximize"),
        "no-closure": ("closure_gate_correct", "maximize"),
        "no-problem": ("problem_contamination_rate", "minimize"),
    }
    baseline = {
        item.get("scenario", {}).get("scenario_id"): item.get("arms", {}).get("SOUL", {}).get("metrics", {})
        for item in d5.get("cases", [])
    }
    ablated = {
        (item.get("ablation"), item.get("scenario_id")): item.get("arms", {}).get("SOUL", {}).get("metrics", {})
        for item in d5.get("ablations", {}).get("cases", [])
    }
    report: dict[str, dict[str, Any]] = {}
    for mode, (metric, direction) in metrics_by_mode.items():
        deltas = []
        for (ablation, scenario_id), metrics in ablated.items():
            before = baseline.get(scenario_id, {})
            if ablation != mode or metric not in before or metric not in metrics:
                continue
            raw_delta = metrics[metric] - before[metric]
            oriented = raw_delta if direction == "minimize" else -raw_delta
            deltas.append(oriented)
        mean = round(sum(deltas) / len(deltas), 6) if deltas else None
        report[mode] = {
            "metric": metric,
            "matched_cases": len(deltas),
            "mean_ablation_penalty": mean,
            "contributes": mean is not None and mean >= 0.01,
            "scope": "paired deterministic fixture/perturbation comparison; not external causal evidence",
        }
    return report


def _transfer_precision_recall(d5: dict[str, Any]) -> dict[str, Any]:
    true_positive = false_positive = false_negative = 0
    observed = 0
    for case in d5.get("cases", []):
        metrics = case.get("arms", {}).get("SOUL", {}).get("metrics", {})
        if not any(key in metrics for key in (
            "transfer_true_positive", "transfer_false_positive", "transfer_false_negative"
        )):
            continue
        observed += 1
        true_positive += int(metrics.get("transfer_true_positive", 0))
        false_positive += int(metrics.get("transfer_false_positive", 0))
        false_negative += int(metrics.get("transfer_false_negative", 0))
    if not observed:
        return {"status": "NOT OBSERVED", "cases": 0, "precision": None, "recall": None}
    precision_denom = true_positive + false_positive
    recall_denom = true_positive + false_negative
    if precision_denom == 0 or recall_denom == 0:
        return {
            "status": "NOT OBSERVED",
            "cases": observed,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": None,
            "recall": None,
            "reason": "no positive transfer examples; precision/recall denominators are zero",
        }
    return {
        "status": "OBSERVED",
        "cases": observed,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(true_positive / precision_denom, 6) if precision_denom else None,
        "recall": round(true_positive / recall_denom, 6) if recall_denom else None,
    }


def _observed_core_provider_families(d5: dict[str, Any]) -> list[str]:
    providers = set()
    for case in d5.get("cases", []):
        for arm in case.get("arms", {}).values():
            response = arm.get("response", {})
            provider = str(response.get("provider", ""))
            model = str(response.get("model", ""))
            if provider or model:
                providers.add(f"{provider or 'unknown'}:{model or 'unknown'}")
    return sorted(providers)


def _threshold_gate(
    value: float | None,
    requirement: str,
    predicate,
    note: str,
) -> dict[str, str]:
    status = "NOT OBSERVED / OPEN GAP" if value is None else ("PASS" if predicate(value) else "FAIL")
    return _gate(status, str(value) if value is not None else "metric absent", requirement + "; " + note)


def _gate(status: str, observed: str, requirement: str) -> dict[str, str]:
    return {"status": status, "observed": observed, "requirement": requirement}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def unsupported_references_gate(rows: list[dict[str, Any]]) -> dict[str, str]:
    """F-0: the model-output-level check, reported as a gate that can actually fail.

    The old conjunct inside `hard_invariants_100_percent` required
    `unsupported_claim_rate == 0.0`, but that metric was true by construction, so the clause
    could never fail. It has been REMOVED from that boolean conjunction rather than replaced:
    a boolean cannot express `not observed`, and a report built from legacy data that predates
    this metric must not be reported as a failed invariant.

    This gate carries the tri-state instead. It is computed from the raw answer text, and an
    arm whose answer names no identifiers is UNSCORED - never rendered as a clean result.
    """

    present = [row for row in rows if "unsupported_reference_rate" in row]
    if not present:
        return _gate(
            "NOT OBSERVED / OPEN GAP",
            "metric absent from the supplied reports (legacy data)",
            "every scored arm must show zero references to evidence it was not given",
        )
    scored = [
        row["unsupported_reference_rate"]
        for row in present
        if isinstance(row.get("unsupported_reference_rate"), int | float)
    ]
    if not scored:
        return _gate(
            "NOT OBSERVED / OPEN GAP",
            f"present but unscored in all {len(present)} row(s): no answer named any identifier",
            "a gate with nothing scored must not pass",
        )
    dirty = [value for value in scored if value != 0.0]
    if dirty:
        return _gate(
            "FAIL",
            f"{len(dirty)} of {len(scored)} scored row(s) cite evidence they were not given "
            f"(max rate {max(dirty)})",
            "zero unsupported references on every scored arm",
        )
    return _gate(
        "PASS",
        f"{len(scored)} scored row(s), all zero; unscored={len(present) - len(scored)}",
        "zero unsupported references on every scored arm",
    )


def _run_full_tests(root: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    match = re.search(r"(\d+) passed(?:,\s*(\d+) failed)?", output)
    summary = match.group(0) if match else "NOT OBSERVED / OPEN GAP"
    return {
        "command": [sys.executable, "-m", "pytest", "-q"],
        "return_code": result.returncode,
        "summary": summary,
        "passed": int(match.group(1)) if match else None,
        "failed": int(match.group(2) or 0) if match else None,
        "output": output[-4000:],
    }


def _write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8")) if path.exists() else "MISSING"


__all__ = ["build_final_report", "render_d9_gate", "render_final_report", "render_v03_boundary", "run_d9_bundle"]
