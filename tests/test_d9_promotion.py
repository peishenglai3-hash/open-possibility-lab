from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from team_soul.kernel import (
    ExperimentLedger,
    ExperimentRun,
    HeldoutPair,
    PromotionStatus,
    evaluate_promotion,
)
from team_soul.config import RuntimeConfig
from team_soul.eval_promotion import run_promotion_dry_run
from team_soul.hashing import sha256_json
from team_soul.storage import Store


def _run() -> ExperimentRun:
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    return ExperimentRun(
        run_id="experiment:d9-test",
        hypothesis="candidate improves provenance without invariant regression",
        baseline_policy="baseline:v1",
        candidate_policy="candidate:v2",
        scenario_ids=tuple(f"heldout:{index}" for index in range(10)),
        model_configuration={"provider": "mock", "model": "mock-v0.1"},
        heldout_split_hash="split-hash",
        metrics={"provenance_coverage": 1.0},
        artifacts={"report": "test"},
        started_at=now,
        finished_at=now,
    )


def _pairs(*, improve: bool = True, regress: bool = False, count: int = 10) -> list[HeldoutPair]:
    rows = []
    for index in range(count):
        delta = -0.2 if regress else (0.1 if improve else 0.0)
        rows.append(
            HeldoutPair(
                scenario_id=f"heldout:{index}",
                evidence_level="E3",
                baseline_metrics={"provenance_coverage": 0.8, "unsupported_selection_rate": 0.1},
                candidate_metrics={
                    "provenance_coverage": 0.8 + delta,
                    "unsupported_selection_rate": 0.1,
                },
            )
        )
    return rows


_OBJECTIVES = {
    "provenance_coverage": "maximize",
    "unsupported_selection_rate": "minimize",
}


def test_promotion_is_human_gated_and_never_auto_promotes() -> None:
    decision = evaluate_promotion(_run(), _pairs(), objective_directions=_OBJECTIVES)

    assert decision.decision == PromotionStatus.ESCALATE
    assert decision.human_gate_required is True
    assert decision.auto_promote is False
    assert decision.pareto_improvements == ("provenance_coverage",)

    approved = evaluate_promotion(
        _run(), _pairs(), objective_directions=_OBJECTIVES, human_approved=True
    )
    assert approved.decision == PromotionStatus.PROMOTE
    assert approved.auto_promote is False


def test_promotion_holds_small_or_non_improving_heldout_sets() -> None:
    small = evaluate_promotion(
        _run(), _pairs(count=6), objective_directions=_OBJECTIVES
    )
    flat = evaluate_promotion(
        _run(), _pairs(improve=False), objective_directions=_OBJECTIVES
    )

    assert small.decision == PromotionStatus.HOLD
    assert "INSUFFICIENT_HELDOUT_CASES" in small.reason_codes
    assert flat.decision == PromotionStatus.HOLD
    assert "NO_MEANINGFUL_PARETO_IMPROVEMENT" in flat.reason_codes


def test_promotion_rejects_hard_gate_or_significant_paired_regression() -> None:
    gate = evaluate_promotion(
        _run(), _pairs(), objective_directions=_OBJECTIVES,
        hard_gate_regressions=("EXTERNAL_VETO_OVERRIDE",),
    )
    regression = evaluate_promotion(
        _run(), _pairs(improve=False, regress=True), objective_directions=_OBJECTIVES
    )

    assert gate.decision == PromotionStatus.REJECT
    assert gate.auto_promote is False
    assert regression.decision == PromotionStatus.REJECT
    assert "SIGNIFICANT_HELDOUT_REGRESSION" in regression.reason_codes


def test_promotion_deduplicates_derived_variants_by_episode() -> None:
    pairs = [
        HeldoutPair(
            scenario_id="SYN-C01",
            episode_id="fixture:C01",
            evidence_level="E1",
            baseline_metrics={"provenance_coverage": 0.8},
            candidate_metrics={"provenance_coverage": 0.9},
        ),
        HeldoutPair(
            scenario_id="PER-C01-reverse_event_order",
            episode_id="fixture:C01",
            evidence_level="E3",
            baseline_metrics={"provenance_coverage": 0.0},
            candidate_metrics={"provenance_coverage": 1.0},
        ),
    ]
    decision = evaluate_promotion(
        _run().model_copy(update={"scenario_ids": ("SYN-C01",)}),
        pairs,
        objective_directions={"provenance_coverage": "maximize"},
        minimum_heldout_cases=2,
        minimum_improvement=0.01,
    )

    assert decision.decision == PromotionStatus.HOLD
    assert "INSUFFICIENT_HELDOUT_CASES" in decision.reason_codes
    assert decision.heldout_results["raw_pair_count"] == 2
    assert decision.heldout_results["unique_episode_count"] == 1
    assert decision.heldout_results["deduplicated_variant_count"] == 1


def test_experiment_ledger_is_append_only_and_separate_from_raw_history() -> None:
    store = Store(":memory:")
    run = _run()
    ledger = ExperimentLedger(store)

    assert ledger.append(run) == run.run_id
    assert ledger.append(run) == run.run_id
    assert ledger.list() == [run]
    assert store.list_events() == []
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("UPDATE experiment_ledger SET payload_json='{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("DELETE FROM experiment_ledger")
    store.close()


def test_experiment_ledger_rejects_conflicting_reuse_of_run_id() -> None:
    store = Store(":memory:")
    ledger = ExperimentLedger(store)
    ledger.append(_run())
    conflicting = _run().model_copy(update={"hypothesis": "different immutable receipt"})

    with pytest.raises(ValueError, match="collision"):
        ledger.append(conflicting)
    store.close()


def test_promotion_dry_run_can_emit_evaluator_owned_receipt(tmp_path: Path) -> None:
    d5 = {
        "generated_at": "2026-09-23T00:00:00+00:00",
        "run_configuration": {"provider_kind": "mock", "provider_model": "mock-v0.1"},
        "cases": [],
    }
    d6 = {
        "generated_at": "2026-09-23T00:01:00+00:00",
        "source_d5_report_hash": sha256_json(d5),
        "split": {"split_hash": "split-hash"},
        "heldout": {"cases": []},
        "failure_scout": {"candidates": []},
    }
    d5_path = tmp_path / "d5.json"
    d6_path = tmp_path / "d6.json"
    d5_path.write_text(json.dumps(d5), encoding="utf-8")
    d6_path.write_text(json.dumps(d6), encoding="utf-8")

    report = run_promotion_dry_run(
        d5_path,
        d6_path,
        RuntimeConfig(data_dir=tmp_path / "runtime"),
        receipt_dir=tmp_path / "experiment_receipts",
    )

    receipt_path = Path(report["experiment_receipt_path"])
    assert receipt_path.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_type"] == "evaluator-owned-promotion-dry-run"
    assert receipt["auto_promote"] is False
    assert receipt["candidate_activated"] is False
