from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .failure_scout import FailureScout
from .heldout import CandidateInput, IsolatedHeldoutEvaluator, deterministic_split
from .hashing import sha256_json
from .projection import rebuild_projection
from .retrieval import retrieve
from .scenario import build_scenario_matrix
from .storage import Store


def run_d6_suite(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    d5_report: str | Path = "docs/EVAL_RESULTS_V02.json",
    output_json: str | Path = "docs/FAILURE_REGISTER.json",
    output_markdown: str | Path = "docs/FAILURE_REGISTER.md",
    failure_budget: int = 20,
    heldout_ratio: float = 0.25,
) -> dict[str, Any]:
    scenarios = build_scenario_matrix(fixture_dir)
    split = deterministic_split(scenarios, heldout_ratio=heldout_ratio)
    source_report = json.loads(Path(d5_report).read_text(encoding="utf-8"))
    train_ids = {item.spec.scenario_id for item in split.train}
    train_results = [
        result
        for result in source_report.get("cases", [])
        if result.get("scenario", {}).get("scenario_id") in train_ids
    ]
    scout = FailureScout()
    failure_candidates = scout.rank(train_results, list(split.train), budget=failure_budget)
    evaluator = IsolatedHeldoutEvaluator()
    train_evaluation = evaluator.evaluate(
        split.train,
        _soul_candidate,
        baseline=_rag_candidate,
        context_budget_tokens=config.context_budget_tokens,
    )
    heldout = evaluator.evaluate(
        split.heldout,
        _soul_candidate,
        baseline=_rag_candidate,
        context_budget_tokens=config.context_budget_tokens,
    )
    overfit_check = _overfit_check(train_evaluation, heldout)
    report = {
        "schema_version": "failure-register.v0.2.d6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_d5_report": str(d5_report),
        "source_d5_report_hash": sha256_json(source_report),
        "scenario_count": len(scenarios),
        "split": {
            "seed": split.seed,
            "split_unit": split.split_unit,
            "train_count": len(split.train),
            "heldout_count": len(split.heldout),
            "train_episode_count": len(split.train_episode_ids),
            "heldout_episode_count": len(split.heldout_episode_ids),
            "train_episode_ids": list(split.train_episode_ids),
            "heldout_episode_ids": list(split.heldout_episode_ids),
            "train_ids": [item.spec.scenario_id for item in split.train],
            "heldout_ids": [item.spec.scenario_id for item in split.heldout],
            "split_hash": split.split_hash,
        },
        "failure_scout": {
            "budget": failure_budget,
            "candidate_count": len(failure_candidates),
            "candidates": [item.model_dump(mode="json") for item in failure_candidates],
            "auto_repair": False,
        },
        "train_evaluation": train_evaluation,
        "heldout": heldout,
        "overfit_check": overfit_check,
        "gates": {
            "group_split_unit_episode": split.split_unit == "episode_id",
            "group_disjoint": split.group_disjoint,
            "split_disjoint": not (
                set(item.spec.scenario_id for item in split.train)
                & set(item.spec.scenario_id for item in split.heldout)
            ),
            "candidate_oracle_input_absent": not heldout.get("candidate_has_oracle_input", True),
            "oracle_isolation": train_evaluation.get("oracle_isolation", False)
            and heldout.get("oracle_isolation", False),
            "raw_history_unchanged": (
                train_evaluation.get("raw_history_hash_before")
                == train_evaluation.get("raw_history_hash_after")
                and heldout.get("raw_history_hash_before")
                == heldout.get("raw_history_hash_after")
            ),
            "candidate_cheating_audit": heldout.get("candidate_audit", {}).get("passed", False),
            "failure_budget_respected": len(failure_candidates) <= failure_budget,
            "overfit_check": overfit_check["status"] == "PASS",
            "heldout_not_in_training_results": not any(
                item["scenario_id"] in train_ids for item in heldout.get("cases", [])
            ),
        },
        "interpretation_boundary": {
            "status": "structural evaluator evidence only",
            "e2": "NOT OBSERVED / OPEN GAP",
            "e4": "NOT OBSERVED / OPEN GAP",
        },
    }
    _write_json(output_json, report)
    Path(output_markdown).parent.mkdir(parents=True, exist_ok=True)
    Path(output_markdown).write_text(render_markdown(report), encoding="utf-8")
    return report


def _soul_candidate(candidate_input: CandidateInput) -> dict[str, Any]:
    return _retrieve(candidate_input, "SOUL")


def _rag_candidate(candidate_input: CandidateInput) -> dict[str, Any]:
    return _retrieve(candidate_input, "RAG")


def _retrieve(candidate_input: CandidateInput, arm: str) -> dict[str, Any]:
    store = Store(":memory:")
    store.init_schema()
    store.append_events(candidate_input.events)
    rebuild_projection(store)
    result = retrieve(
        store,
        candidate_input.query,
        arm,
        RuntimeConfig(context_budget_tokens=candidate_input.context_budget_tokens),
    )
    selected = [item.event_id for item in result.selected]
    store.close()
    return {"selected_event_ids": selected, "arm": arm}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.2 Failure Register",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Scenario split: `{report['split']['split_hash']}`",
        "",
        "## Gate summary",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for name, value in report["gates"].items():
        lines.append(f"| {name} | {'PASS' if value is True else value} |")
    lines.extend(
        [
            "",
            "## FailureScout",
            "",
            f"- Candidates: `{report['failure_scout']['candidate_count']}` / budget `{report['failure_scout']['budget']}`",
            "- Auto repair: `false`; candidates only enter the experiment queue.",
            "",
            "| Rank | Scenario | Score | Reasons |",
            "|---:|---|---:|---|",
        ]
    )
    for candidate in report["failure_scout"]["candidates"]:
        lines.append(
            f"| {candidate['selected_rank']} | {candidate['scenario_id']} | {candidate['score']} | "
            f"{', '.join(candidate['reason_codes']) or 'below threshold'} |"
        )
    lines.extend(
        [
            "",
            "## Held-out boundary",
            "",
            f"- Held-out cases: `{report['split']['heldout_count']}`",
            f"- Split unit: `{report['split'].get('split_unit')}`; unique episodes train/held-out: `"
            f"{report['split'].get('train_episode_count')}/{report['split'].get('heldout_episode_count')}`",
            f"- Group disjoint: `{report['gates'].get('group_disjoint')}`",
            f"- Evaluator status: `{report['heldout'].get('status')}`",
            f"- Oracle isolation: `{report['heldout'].get('oracle_isolation')}`",
            f"- Candidate oracle input: `{report['heldout'].get('candidate_has_oracle_input')}`",
            f"- Overfit screen: `{report['overfit_check'].get('status')}`",
            "",
            "## Interpretation boundary",
            "",
            "This is structural evaluator evidence. It is not E2 historical evidence, "
            "not an E4 live pilot, and not evidence of real-world utility.",
            "",
        ]
    )
    return "\n".join(lines)


def _overfit_check(train: dict[str, Any], heldout: dict[str, Any]) -> dict[str, Any]:
    """Screen for train uplift that reverses on held-out/perturbation cases."""

    train_scores = _score_delta(train.get("cases", []))
    heldout_scores = _score_delta(heldout.get("cases", []))
    perturbation_rows = [
        row for row in heldout.get("cases", []) if row.get("evidence_level") == "E3"
    ]
    perturbation_scores = _score_delta(perturbation_rows)
    if train_scores is None or heldout_scores is None:
        return {
            "status": "NOT OBSERVED / OPEN GAP",
            "rule": "train uplift > 0 and held-out or perturbation uplift < 0 => OVERFIT",
            "train": train_scores,
            "heldout": heldout_scores,
            "perturbation": perturbation_scores,
        }
    train_uplift = train_scores["candidate"] - train_scores["baseline"]
    heldout_uplift = heldout_scores["candidate"] - heldout_scores["baseline"]
    perturbation_uplift = (
        perturbation_scores["candidate"] - perturbation_scores["baseline"]
        if perturbation_scores is not None
        else None
    )
    overfit = train_uplift > 0 and (
        heldout_uplift < 0 or (perturbation_uplift is not None and perturbation_uplift < 0)
    )
    return {
        "status": "OVERFIT" if overfit else "PASS",
        "rule": "train uplift > 0 and held-out or perturbation uplift < 0 => OVERFIT",
        "metric": "provenance_coverage",
        "train": train_scores,
        "heldout": heldout_scores,
        "perturbation": perturbation_scores,
        "train_uplift": round(train_uplift, 6),
        "heldout_uplift": round(heldout_uplift, 6),
        "perturbation_uplift": (
            round(perturbation_uplift, 6) if perturbation_uplift is not None else None
        ),
    }


def _score_delta(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    candidate = [
        float(row.get("oracle_metrics", {}).get("provenance_coverage", 0.0))
        for row in rows
        if row.get("oracle_metrics") is not None
    ]
    baseline = [
        float(row.get("baseline_oracle_metrics", {}).get("provenance_coverage", 0.0))
        for row in rows
        if row.get("baseline_oracle_metrics") is not None
    ]
    if not candidate or not baseline:
        return None
    return {
        "case_count": float(min(len(candidate), len(baseline))),
        "candidate": round(sum(candidate) / len(candidate), 6),
        "baseline": round(sum(baseline) / len(baseline), 6),
    }


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = ["run_d6_suite", "render_markdown"]
