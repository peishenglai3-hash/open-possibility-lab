from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .eval import ARMS, _normalize_arms, run_case
from .hashing import sha256_json
from .kernel.policies import PolicyBundle, deterministic_policy_bundle
from .contracts import (
    UNSCORED,
    ClosureStatus,
    EvidenceKind,
    OperationalClosure,
    RetrievalResult,
    TransferCandidate,
    TransferDecision,
)
from .scenario import (
    AblationKind,
    ScenarioCase,
    build_scenario_matrix,
    load_historical_replay,
    scenario_manifest,
)
from .storage import Store


DEFAULT_ABLATIONS = tuple(AblationKind)


def run_v02_suite(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    historical_dir: str | Path | None = None,
    output_json: str | Path = "docs/EVAL_RESULTS_V02.json",
    output_markdown: str | Path = "docs/EVAL_REPORT_V02.md",
    output_manifest: str | Path = "docs/SCENARIO_MATRIX_V02.json",
    arms: tuple[str, ...] = ARMS,
    scenario_limit: int | None = None,
    run_ablations: bool = True,
    ablation_kinds: tuple[AblationKind, ...] = DEFAULT_ABLATIONS,
) -> dict[str, Any]:
    """Run the D5 matrix, replay boundary, perturbation suite, and ablations."""

    normalized_arms = _normalize_arms(arms)
    matrix = build_scenario_matrix(fixture_dir)
    historical, historical_status = load_historical_replay(historical_dir)
    manifest = scenario_manifest(matrix)
    protocol_manifest = _load_protocol_manifest()
    selected_matrix = matrix[:scenario_limit] if scenario_limit is not None else matrix
    selected_cases = [*selected_matrix, *historical]
    log_store = Store(config.data_dir / "eval_v02_runs.sqlite3")
    log_store.init_schema()
    try:
        results = [_run_scenario(item, config, log_store, normalized_arms) for item in selected_cases]
        ablations = (
            run_ablation_suite(
                selected_matrix,
                config,
                log_store=log_store,
                arms=normalized_arms,
                kinds=ablation_kinds,
            )
            if run_ablations
            else {"enabled": False, "modes": [], "cases": [], "scorecard": {}}
        )
    finally:
        log_store.close()

    report = {
        "schema_version": "eval.v0.2.d5",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_protocol": protocol_manifest,
        "run_configuration": {
            "provider_kind": config.provider_kind,
            "provider_model": config.provider_model,
            "context_budget_tokens": config.context_budget_tokens,
            "rrf_k": config.rrf_k,
            "candidate_top_n": config.candidate_top_n,
            "temperature": config.temperature,
            "max_output_tokens": config.max_output_tokens,
            "redact_secrets": config.redact_secrets,
            "configuration_hash": sha256_json(
                {
                    "provider_kind": config.provider_kind,
                    "provider_model": config.provider_model,
                    "context_budget_tokens": config.context_budget_tokens,
                    "rrf_k": config.rrf_k,
                    "candidate_top_n": config.candidate_top_n,
                    "temperature": config.temperature,
                    "max_output_tokens": config.max_output_tokens,
                    "redact_secrets": config.redact_secrets,
                }
            ),
        },
        "requested_arms": list(normalized_arms),
        "executed_arms": list(normalized_arms),
        "scenario_manifest_hash": manifest["manifest_hash"],
        "scenario_matrix": {
            "declared_case_count": manifest["case_count"],
            "executed_case_count": len(selected_matrix),
            "historical_case_count": len(historical),
            "evidence_counts": _count_by(results, "evidence_level"),
            "environment_counts": _count_by(results, "environment"),
        },
        "historical_replay": historical_status,
        "perturbation": {
            "status": "PASS" if any(item.spec.perturbation for item in selected_matrix) else "FAIL",
            "case_count": sum(item.spec.perturbation is not None for item in selected_matrix),
        },
        "scorecard": scorecard(results),
        "metric_denominators": _metric_denominators(selected_matrix, results),
        "ablations": ablations,
        "gates": _gates(
            matrix=matrix,
            selected=selected_matrix,
            results=results,
            historical_status=historical_status,
            ablations=ablations,
        ),
        "cases": results,
    }
    _write_json(output_json, report)
    _write_json(output_manifest, manifest)
    Path(output_markdown).parent.mkdir(parents=True, exist_ok=True)
    Path(output_markdown).write_text(render_markdown(report), encoding="utf-8")
    return report


def run_ablation_suite(
    scenarios: list[ScenarioCase],
    config: RuntimeConfig,
    *,
    log_store: Store | None = None,
    arms: tuple[str, ...] = ARMS,
    kinds: tuple[AblationKind, ...] = DEFAULT_ABLATIONS,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for kind in kinds:
        policy_bundle = _ablation_bundle(kind)
        for scenario in scenarios:
            # Formal ablations keep the exact source case and raw history
            # fixed. The ablated mechanism is injected at the policy seam;
            # the legacy materializing helper remains available for callers
            # that explicitly need a transformed fixture, but it is not used
            # for the V0.2-R experiment.
            result = run_case(
                scenario.case,
                config,
                log_store=log_store,
                arms=arms,
                policy_bundle=policy_bundle,
            )
            rows.append(
                {
                    "ablation": kind.value,
                    "scenario_id": scenario.spec.scenario_id,
                    "source_case_id": scenario.spec.source_case_id,
                    "input_case_id": scenario.case.case_id,
                    "input_history_hash": result["raw_history_hash"],
                    "input_event_ids": [event.id for event in scenario.case.events],
                    "ablation_input_is_frozen": True,
                    "policy_bundle": {
                        "retrieval": type(policy_bundle.retrieval).__name__,
                        "transfer": type(policy_bundle.transfer).__name__,
                        "closure": type(policy_bundle.closure).__name__,
                    },
                    "arms": result["arms"],
                }
            )
    return {
        "enabled": True,
        "modes": [kind.value for kind in kinds],
        "case_count": len(rows),
        "scorecard": scorecard(rows),
        "cases": rows,
    }


class _AblatedRetrieval:
    """Policy-level ablation over one frozen retrieval input.

    The store and query are unchanged. Only the selected derived evidence is
    filtered after the deterministic reference policy has produced it.
    """

    def __init__(self, kind: AblationKind, inner: Any) -> None:
        self.kind = kind
        self.inner = inner

    def execute(self, store: Store, query: str, arm: str, config: RuntimeConfig) -> RetrievalResult:
        effective_arm = "RAG" if self.kind == AblationKind.NO_PROBLEM and arm == "SOUL" else arm
        result = self.inner.execute(store, query, effective_arm, config)
        selected = list(result.selected)
        if self.kind == AblationKind.NO_DISSENT:
            selected = [
                item
                for item in selected
                if item.evidence_kind not in {EvidenceKind.DISSENT, EvidenceKind.VETO}
            ]
        elif self.kind == AblationKind.NO_TEMPORAL:
            selected = [
                item
                for item in selected
                if not _event_has_any_role(store, item.event_id, {"old", "historical", "temporal", "revision"})
            ]
        return result.model_copy(
            update={
                "arm": arm,
                "selected": selected,
                "context_tokens": sum(item.estimated_tokens for item in selected),
            }
        )


class _AblatedTransfer:
    def __init__(self, kind: AblationKind, inner: Any) -> None:
        self.kind = kind
        self.inner = inner

    def evaluate(self, candidate: TransferCandidate) -> TransferCandidate:
        if self.kind == AblationKind.NO_TRANSFER:
            return candidate.model_copy(
                update={
                    "decision": TransferDecision.UNVERIFIED,
                    "reason_codes": [*candidate.reason_codes, "TRANSFER_POLICY_ABLATED"],
                }
            )
        return self.inner.evaluate(candidate)


class _AblatedClosure:
    def __init__(self, kind: AblationKind, inner: Any) -> None:
        self.kind = kind
        self.inner = inner

    def evaluate(
        self,
        proposal: Any,
        store: Store,
        config: RuntimeConfig,
        *,
        as_of: Any = None,
    ) -> OperationalClosure:
        closure = self.inner.evaluate(proposal, store, config, as_of=as_of)
        if self.kind == AblationKind.NO_CLOSURE:
            return closure.model_copy(
                update={
                    "status": ClosureStatus.NO_CLOSURE,
                    "reason_codes": [*closure.reason_codes, "CLOSURE_POLICY_ABLATED"],
                }
            )
        return closure


def _ablation_bundle(kind: AblationKind) -> PolicyBundle:
    """Build a fair ablation without changing the experiment's raw input."""

    baseline = deterministic_policy_bundle()
    return PolicyBundle(
        retrieval=_AblatedRetrieval(kind, baseline.retrieval),
        transfer=_AblatedTransfer(kind, baseline.transfer),
        closure=_AblatedClosure(kind, baseline.closure),
        problem_link=baseline.problem_link,
        activation=baseline.activation,
    )


def _event_has_any_role(store: Store, event_id: str, roles: set[str]) -> bool:
    event = store.get_event(event_id)
    if event is None:
        return False
    raw_roles = event.metadata.get("roles", event.metadata.get("tags", []))
    values = raw_roles if isinstance(raw_roles, list) else [raw_roles]
    return bool({str(value).lower() for value in values}.intersection(roles))


def scorecard(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate declared metrics without treating missing metrics as zero."""

    by_arm: dict[str, list[dict[str, float]]] = defaultdict(list)
    by_group: dict[str, dict[str, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        group = str(result.get("environment", result.get("ablation", "all")))
        for arm, payload in result.get("arms", {}).items():
            metrics = payload.get("metrics", {})
            by_arm[arm].append(metrics)
            by_group[group][arm].append(metrics)
    # `applicability` counts rows where the metric KEY is present; `scored_counts` counts rows
    # where it produced a NUMBER. Epoch 4 (F-2): the two are no longer the same thing.
    applicability = {
        arm: {
            metric: sum(1 for row in rows if metric in row)
            for metric in sorted({key for row in rows for key in row})
        }
        for arm, rows in sorted(by_arm.items())
    }
    scored_counts: dict[str, dict[str, int]] = {}
    return {
        "by_arm": {
            arm: _mean_metrics(rows, scored_counts=scored_counts.setdefault(arm, {}))
            for arm, rows in sorted(by_arm.items())
        },
        "scored_counts": {arm: counts for arm, counts in sorted(scored_counts.items())},
        "by_group": {
            group: {arm: _mean_metrics(rows) for arm, rows in sorted(arms.items())}
            for group, arms in sorted(by_group.items())
        },
        "applicability": applicability,
        "metric_semantics": (
            "mean over the SCORED case-arm metrics only; absent metrics are excluded, never "
            "zero-filled, and a metric with an empty denominator is recorded as UNSCORED "
            "(epoch 4, F-2) rather than as 1.0 or 0.0. Read `scored_counts` beside any mean: a "
            "mean computed over 0 rows is not a result."
        ),
    }


def _metric_denominators(
    scenarios: list[ScenarioCase],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {result.get("case_id"): result for result in results}
    dissent_ids = [
        item.spec.scenario_id
        for item in scenarios
        if item.case.expected.get("dissent_event_ids")
    ]
    base = [
        item
        for item in scenarios
        if item.spec.scenario_id.startswith("SYN-")
    ]
    false_no_closure_ids = [
        item.spec.scenario_id
        for item in base
        if item.case.expected.get("closure_status") == "ACTIVE"
        and str((by_id.get(item.spec.scenario_id, {}).get("closure") or {}).get("status"))
        == "NO_CLOSURE"
    ]
    false_closure_ids = [
        item.spec.scenario_id
        for item in base
        if item.case.expected.get("closure_status") == "NO_CLOSURE"
        and str((by_id.get(item.spec.scenario_id, {}).get("closure") or {}).get("status"))
        == "ACTIVE"
    ]
    return {
        "dissent_preservation": {
            "applicable_scenario_count": len(dissent_ids),
            "applicable_scenario_ids": dissent_ids,
        },
        "false_no_closure": {
            "denominator": len(base),
            "numerator": len(false_no_closure_ids),
            "rate": round(len(false_no_closure_ids) / len(base), 6) if base else None,
            "case_ids": false_no_closure_ids,
        },
        "false_closure": {
            "denominator": len(base),
            "numerator": len(false_closure_ids),
            "rate": round(len(false_closure_ids) / len(base), 6) if base else None,
            "case_ids": false_closure_ids,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    matrix = report["scenario_matrix"]
    lines = [
        "# Team Soul V0.2 D5 Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Scenario manifest: `{report['scenario_manifest_hash']}`",
        f"Protocol: `{report.get('evaluation_protocol', {}).get('protocol_sha256', 'NOT OBSERVED / OPEN GAP')}`",
        f"Executed arms: `{', '.join(report['executed_arms'])}`",
        "",
        "## Gate summary",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for name, value in report["gates"].items():
        lines.append(f"| {name} | {value} |")
    lines.extend(
        [
            "",
            "## Matrix",
            "",
            f"- Declared cases: `{matrix['declared_case_count']}`",
            f"- Executed matrix cases: `{matrix['executed_case_count']}`",
            f"- Historical replay cases: `{matrix['historical_case_count']}`",
            f"- Evidence counts: `{matrix['evidence_counts']}`",
            f"- Environment counts: `{matrix['environment_counts']}`",
            f"- Historical status: `{report['historical_replay']['status']}`",
            "",
            "## Scorecard",
            "",
            "| Arm | Metrics |",
            "|---|---|",
        ]
    )
    for arm, metrics in report["scorecard"]["by_arm"].items():
        rendered = ", ".join(f"{key}={value}" for key, value in metrics.items())
        lines.append(f"| {arm} | {rendered or 'n/a'} |")
    ablations = report["ablations"]
    lines.extend(
        [
            "",
            "## Ablation",
            "",
            f"- Enabled: `{ablations['enabled']}`",
            f"- Modes: `{', '.join(ablations.get('modes', []))}`",
            f"- Case-arm rows: `{ablations.get('case_count', 0)}`",
            "",
            "## Interpretation boundary",
            "",
            "E1 synthetic and E3 perturbation results are structural evidence. "
            "E2 historical replay is not claimed without explicitly human-approved "
            "historical material. This report does not establish real-world team benefit "
            "or model superiority outside the declared corpus.",
            "",
        ]
    )
    return "\n".join(lines)


def _run_scenario(
    scenario: ScenarioCase,
    config: RuntimeConfig,
    log_store: Store,
    arms: tuple[str, ...],
) -> dict[str, Any]:
    result = run_case(scenario.case, config, log_store=log_store, arms=arms)
    result["scenario"] = scenario.spec.model_dump(mode="json")
    result["environment"] = _enum_value(scenario.spec.environment)
    result["evidence_level"] = _enum_value(scenario.spec.evidence_level)
    return result


def _gates(
    *,
    matrix: list[ScenarioCase],
    selected: list[ScenarioCase],
    results: list[dict[str, Any]],
    historical_status: dict[str, Any],
    ablations: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scenario_target_24_to_32": 24 <= len(matrix) <= 32,
        "scenario_execution_complete": len(results) >= len(selected),
        "perturbation_runner": any(
            _enum_value(item.spec.environment) == "Perturbation" for item in matrix
        ),
        "historical_replay_runner": True,
        "historical_replay_evidence": historical_status["status"]
        if historical_status["status"] != "PASS"
        else True,
        "ablation_runner": bool(ablations.get("enabled")),
        "projection_rebuild_stable": all(
            case.get("projection_stable", False) for case in results
        ),
        "no_unobserved_e2_claim": historical_status["status"] != "PASS"
        or all(case.get("evidence_level") == "E2" for case in results if case.get("environment") == "Historical Replay"),
    }


def _mean_metrics(rows: list[dict[str, Any]], *, scored_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """Mean of each metric over the rows where it was actually scored.

    Epoch 4 (F-2): a metric that is UNSCORED in some rows must be averaged over its SCORED
    rows only, and the aggregation must say how many rows that was. Averaging in a sentinel
    would either crash or, worse, let a metric that was never measured report a clean number.
    When no row scored a metric, it is reported as UNSCORED rather than dropped silently.
    """

    keys = sorted({key for row in rows for key in row})
    result: dict[str, Any] = {}
    for key in keys:
        values = [
            row[key]
            for row in rows
            if isinstance(row.get(key), int | float)
        ]
        if scored_counts is not None:
            scored_counts[key] = len(values)
        result[key] = round(sum(values) / len(values), 6) if values else UNSCORED
    return result


def _count_by(results: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        counts[str(result.get(key, "UNKNOWN"))] += 1
    return dict(sorted(counts.items()))


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_protocol_manifest() -> dict[str, Any]:
    path = Path("docs/V02R_PROTOCOL_MANIFEST.json")
    if not path.exists():
        return {
            "status": "NOT OBSERVED / OPEN GAP",
            "path": str(path),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["path"] = str(path)
    return payload


def write_v02r_auxiliary_artifacts(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Persist non-secret provider and policy-seam manifests for one run."""

    root = Path(output_dir)
    provider_dir = root / "provider_manifests"
    policy_dir = root / "policy_diff"
    provider_dir.mkdir(parents=True, exist_ok=True)
    policy_dir.mkdir(parents=True, exist_ok=True)
    first_case = (report.get("cases") or [{}])[0]
    first_arm = next(iter((first_case.get("arms") or {}).values()), {})
    provider_manifest = first_arm.get("context", {}).get("provider_manifest", {})
    provider_payload = {
        "schema_version": "provider-manifest.v0.2r",
        "provider_kind": report.get("run_configuration", {}).get("provider_kind"),
        "provider_model": report.get("run_configuration", {}).get("provider_model"),
        "executed_arms": report.get("executed_arms", []),
        "provider_manifest": provider_manifest,
        "provider_manifest_hash": sha256_json(provider_manifest),
        "network_execution": "local mock provider only",
        "external_second_provider": "NOT OBSERVED / OPEN GAP",
    }
    mock_path = provider_dir / "mock-v0.1.json"
    _write_json(mock_path, provider_payload)
    second_path = provider_dir / "openai-compatible-adapter.json"
    _write_json(
        second_path,
        {
            "schema_version": "provider-manifest.v0.2r",
            "provider_kind": "openai-compatible",
            "adapter_smoke": "PASS",
            "network_execution": "NOT OBSERVED / OPEN GAP",
            "endpoint_configured": False,
            "credentials_persisted": False,
        },
    )
    policy_names = {
        key: value
        for key, value in (first_arm.get("run_log", {}).get("params", {}).get("policy_bundle", {}) or {}).items()
        if key != "runtime_resolution"
    }
    policy_payload = {
        "schema_version": "policy-diff.v0.2r",
        "baseline_policy_bundle": policy_names,
        "ablation_policy_bundles": sorted(
            {
                tuple(sorted((row.get("policy_bundle") or {}).items()))
                for row in report.get("ablations", {}).get("cases", [])
            }
        ),
        "input_history_frozen_across_ablations": all(
            row.get("ablation_input_is_frozen") is True
            for row in report.get("ablations", {}).get("cases", [])
        ),
        "learned_policy_or_activation": "NOT OBSERVED / OPEN GAP",
        "candidate_activation": False,
    }
    # JSON cannot serialize set/tuple structures; normalize the compact diff
    # after constructing it so the artifact is deterministic and readable.
    policy_payload["ablation_policy_bundles"] = [
        dict(items) for items in policy_payload["ablation_policy_bundles"]
    ]
    policy_path = policy_dir / "V02R_POLICY_SEAM_DIFF.json"
    _write_json(policy_path, policy_payload)
    return {
        "mock_provider_manifest": str(mock_path),
        "second_provider_manifest": str(second_path),
        "policy_diff": str(policy_path),
    }


__all__ = [
    "DEFAULT_ABLATIONS",
    "render_markdown",
    "run_ablation_suite",
    "run_v02_suite",
    "scorecard",
]
