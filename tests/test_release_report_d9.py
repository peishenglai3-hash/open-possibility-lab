from __future__ import annotations

from team_soul.release_report import build_final_report, _transfer_precision_recall


def _inputs(*, positive_transfer: bool = True):
    base_gates = {
        "scenario_target_24_to_32": True,
        "scenario_execution_complete": True,
        "perturbation_runner": True,
        "ablation_runner": True,
        "projection_rebuild_stable": True,
        "no_unobserved_e2_claim": True,
    }
    shared_metrics = {
        "negative_transfer_rejection": 1.0,
        "team_level_overreach": 0.0,
        "false_closure_rate": 0.0,
        "unsupported_claim_rate": 0.0,
        "dissent_preservation_rate": 0.95,
        "false_no_closure_rate": 0.02,
        "provenance_coverage": 0.96,
        "transfer_true_positive": 1.0 if positive_transfer else 0.0,
        "transfer_false_positive": 0.0,
        "transfer_false_negative": 0.0,
    }
    arms = {arm: {"metrics": dict(shared_metrics)} for arm in ("BASE", "RAG", "SOUL")}
    d5 = {
        "scenario_matrix": {"declared_case_count": 26, "evidence_counts": {"E1": 5, "E3": 21}},
        "gates": base_gates,
        "scorecard": {"by_arm": {"SOUL": shared_metrics}},
        "executed_arms": ["BASE", "RAG", "SOUL"],
        "scenario_manifest_hash": "matrix-hash",
        "cases": [
            {"scenario": {"scenario_id": "SYN-C01"}, "arms": arms},
            {"scenario": {"scenario_id": "SYN-C02"}, "arms": arms},
        ],
        "ablations": {"modes": [], "cases": []},
    }
    d6 = {
        "gates": {key: True for key in (
            "split_disjoint", "candidate_oracle_input_absent", "oracle_isolation",
            "raw_history_unchanged", "candidate_cheating_audit", "failure_budget_respected",
            "overfit_check", "heldout_not_in_training_results"
        )},
        "split": {"split_hash": "split-hash"},
        "heldout": {"raw_history_hash_before": "raw", "raw_history_hash_after": "raw"},
    }
    d7 = {
        "gates": {
            "reasoning_trace_schema": True,
            "direct_vs_structured_modes": True,
            "no_private_cot_persistence": True,
            "reasoning_scaffold_utility": "NOT OBSERVED / OPEN GAP",
        },
        "provider_boundary": {
            "primary": {"kind": "mock", "model": "fixture"},
            "second": {"adapter_available": True},
            "execution_status": "NOT OBSERVED / OPEN GAP",
        },
    }
    d8 = {
        "all_suggestions_proposals": True,
        "raw_history_unchanged": True,
        "e4_live_pilot": "NOT OBSERVED / OPEN GAP",
        "real_failure_repair": {"status": "NOT OBSERVED / OPEN GAP"},
    }
    promotion = {
        "promotion_decision": {"decision": "HOLD", "auto_promote": False},
        "auto_promote": False,
        "candidate_activated": False,
        "raw_history_unchanged": True,
    }
    tests = {"return_code": 0, "passed": 56, "failed": 0, "summary": "56 passed"}
    return d5, d6, d7, d8, promotion, tests


def test_final_report_does_not_fail_exercised_invariants_for_missing_legacy_keys() -> None:
    report = build_final_report(*_inputs())

    assert report["gate_summary"]["hard_invariants_100_percent"]["status"] == "PASS"
    assert report["gate_summary"]["historical_e2_min_8"]["status"] == "NOT OBSERVED / OPEN GAP"
    assert report["overall_gate"] == "HOLD"


def test_transfer_precision_recall_is_open_when_positive_class_is_absent() -> None:
    d5, *_ = _inputs(positive_transfer=False)
    result = _transfer_precision_recall(d5)

    assert result["status"] == "NOT OBSERVED"
    assert result["precision"] is None
    assert result["recall"] is None
