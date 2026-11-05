from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .hashing import sha256_json
from .kernel import ExperimentLedger, ExperimentRun, HeldoutPair, evaluate_promotion
from .storage import Store


PROMOTION_OBJECTIVES = {
    "provenance_coverage": "maximize",
    "dissent_preservation": "maximize",
    "temporal_revision_accuracy": "maximize",
    "unsupported_selection_rate": "minimize",
}


def run_promotion_dry_run(
    d5_path: str | Path,
    d6_path: str | Path,
    config: RuntimeConfig,
    *,
    receipt_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a promotion receipt from the evaluator-owned paired held-out report."""

    d5 = json.loads(Path(d5_path).read_text(encoding="utf-8"))
    d6 = json.loads(Path(d6_path).read_text(encoding="utf-8"))
    if d6.get("source_d5_report_hash") != sha256_json(d5):
        raise ValueError("D6 report does not match the supplied frozen D5 report")
    heldout = d6.get("heldout", {})
    rows = heldout.get("cases", [])
    pairs = [
        HeldoutPair(
            scenario_id=str(row["scenario_id"]),
            evidence_level=str(row.get("evidence_level", "UNKNOWN")),
            baseline_metrics=row.get("baseline_oracle_metrics", {}),
            candidate_metrics=row.get("oracle_metrics", {}),
            episode_id=str(row.get("episode_id") or f"episode:{row['scenario_id']}"),
            sample_identity=str(row.get("sample_identity", "")),
        )
        for row in rows
        if isinstance(row.get("baseline_oracle_metrics"), dict)
        and isinstance(row.get("oracle_metrics"), dict)
    ]
    split_hash = str(d6.get("split", {}).get("split_hash", ""))
    if not split_hash:
        raise ValueError("D6 report is missing the deterministic held-out split hash")

    d5_config = d5.get("run_configuration", {})
    source_generated_at = _report_time(d6.get("generated_at"))
    started = datetime.now(timezone.utc)
    experiment_seed = {
        "source_d5_report_hash": sha256_json(d5),
        "source_d6_report_hash": sha256_json(d6),
        "source_d6_generated_at": source_generated_at.isoformat(),
        "heldout_split_hash": split_hash,
        "scenario_ids": [pair.scenario_id for pair in pairs],
        "promotion_started_at": started.isoformat(),
    }
    run_id = f"experiment:{sha256_json(experiment_seed)[:24]}"
    summary_metrics = _aggregate_candidate_metrics(pairs)
    failure_candidates = d6.get("failure_scout", {}).get("candidates", [])
    run = ExperimentRun(
        run_id=run_id,
        hypothesis="SOUL arm improves distributed evidence handling over RAG without invariant regression",
        baseline_policy="RAG:deterministic-baseline",
        candidate_policy="SOUL:deterministic-reference-candidate",
        scenario_ids=tuple(pair.scenario_id for pair in pairs),
        model_configuration={
            "provider_kind": d5_config.get("provider_kind", config.provider_kind),
            "provider_model": d5_config.get("provider_model", config.provider_model),
            "context_budget_tokens": d5_config.get(
                "context_budget_tokens", config.context_budget_tokens
            ),
            "evidence_boundary": "D6 held-out paired fixture metrics; oracle remains evaluator-side",
        },
        heldout_split_hash=split_hash,
        metrics=summary_metrics,
        failure_ids=tuple(
            sorted(
                str(item.get("failure_id", item.get("candidate_id")))
                for item in failure_candidates
                if item.get("failure_id") or item.get("candidate_id")
            )
        ),
        artifacts={
            "d5_report": str(d5_path),
            "d5_report_hash": sha256_json(d5),
            "d6_report": str(d6_path),
            "d6_report_hash": sha256_json(d6),
        },
        started_at=started,
        finished_at=started,
    )

    store = Store(config.sqlite_path)
    store.init_schema()
    raw_before = _raw_hash(store)
    ledger = ExperimentLedger(store)
    ledger.append(run)
    raw_after = _raw_hash(store)
    stored_ids = [item.run_id for item in ledger.list()]
    store.close()

    decision = evaluate_promotion(
        run,
        pairs,
        objective_directions=PROMOTION_OBJECTIVES,
        invariant_regressions=(),
        hard_gate_regressions=(),
        minimum_heldout_cases=8,
        minimum_improvement=0.01,
        human_approved=False,
    )
    report = {
        "schema_version": "promotion-dry-run.v0.2.d9",
        "experiment_run": run.model_dump(mode="json"),
        "promotion_decision": decision.model_dump(mode="json"),
        "promotion_evidence_boundary": decision.heldout_results.get("deduplication", {}),
        "experiment_ledger_count": len(stored_ids),
        "experiment_run_persisted": run.run_id in stored_ids,
        "raw_history_hash_before": raw_before,
        "raw_history_hash_after": raw_after,
        "raw_history_unchanged": raw_before == raw_after,
        "auto_promote": False,
        "candidate_activated": False,
        "interpretation_boundary": (
            "This dry-run compares the frozen evaluator's SOUL and RAG outputs. "
            "It is not an independent learned candidate, E2 historical evidence, "
            "or authorization to activate a policy."
        ),
    }
    if receipt_dir is not None:
        target_dir = Path(receipt_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = target_dir / f"{run.run_id.replace(':', '_')}.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": "experiment-receipt.v0.2r",
                    "receipt_type": "evaluator-owned-promotion-dry-run",
                    "experiment_run": report["experiment_run"],
                    "promotion_decision": report["promotion_decision"],
                    "raw_history_hash_before": report["raw_history_hash_before"],
                    "raw_history_hash_after": report["raw_history_hash_after"],
                    "raw_history_unchanged": report["raw_history_unchanged"],
                    "auto_promote": False,
                    "candidate_activated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report["experiment_receipt_path"] = str(receipt_path)
    return report


def _report_time(value: str | None) -> datetime:
    if value is None:
        raise ValueError("source report is missing generated_at")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("source report generated_at must be timezone-aware")
    return result


def _aggregate_candidate_metrics(pairs: list[HeldoutPair]) -> dict[str, float]:
    metrics = sorted(
        {key for pair in pairs for key in pair.candidate_metrics}
    )
    return {
        key: round(
            sum(pair.candidate_metrics[key] for pair in pairs if key in pair.candidate_metrics)
            / sum(key in pair.candidate_metrics for pair in pairs),
            8,
        )
        for key in metrics
        if any(key in pair.candidate_metrics for pair in pairs)
    }


def _raw_hash(store: Store) -> str:
    return sha256_json([(event.id, event.content_hash) for event in store.list_events()])


__all__ = ["run_promotion_dry_run"]
