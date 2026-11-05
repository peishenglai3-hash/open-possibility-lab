from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .context import assemble_context
from .contracts import UNSCORED, EvalCase, RunLog
from .hashing import sha256_json, sha256_text
from .ids import new_id
from .ingest import load_fixtures
from .operators import check_reopen
from .kernel.policies import PolicyBundle, PolicyRuntime, deterministic_policy_bundle
from .privacy import redact_secrets
from .providers import make_provider
from .reasoning import ReasoningMode
from .storage import Store


ARMS = ("BASE", "RAG", "SOUL")


def run_case(
    case: EvalCase,
    config: RuntimeConfig,
    log_store: Store | None = None,
    *,
    arms: tuple[str, ...] = ARMS,
    policy_bundle: PolicyBundle | None = None,
    policy_runtime: PolicyRuntime | None = None,
    scaffold: ReasoningMode | str = ReasoningMode.DIRECT,
) -> dict[str, Any]:
    if policy_bundle is not None and policy_runtime is not None:
        raise ValueError("pass either policy_bundle or policy_runtime, not both")
    policies = (
        policy_bundle
        or (policy_runtime.bundle() if policy_runtime is not None else deterministic_policy_bundle())
    )
    # ignore_cleanup_errors: on Windows the per-case sqlite file can still be locked when the
    # temporary directory is removed, which used to abort an entire evaluation run (and, in the
    # paid provider runs, lose every call already made). The flag changes no result: it only
    # stops a cleanup failure from being reported as an evaluation failure.
    with tempfile.TemporaryDirectory(
        prefix=f"team-soul-{case.case_id}-", ignore_cleanup_errors=True
    ) as temp_dir:
        case_store = Store(Path(temp_dir) / "case.sqlite3")
        case_store.init_schema()
        case_store.append_events(case.events)
        raw_history_hash = sha256_json(
            [(event.id, event.content_hash) for event in case_store.list_events()]
        )
        policies.problem_link.rebuild(case_store)
        first_hash = _projection_hash(case_store)
        policies.problem_link.rebuild(case_store)
        second_hash = _projection_hash(case_store)
        projection_stable = first_hash == second_hash

        transfer = policies.transfer.evaluate(case.transfer) if case.transfer else None
        # FR-04: the judgement is time-bound to the case corpus, so a TEAM decision can
        # never rest on evidence dated after the corpus it was asked about. A case may
        # declare its real decision time explicitly (E3 future-leak perturbations do);
        # otherwise the latest event timestamp remains the decision time.
        decision_time = (
            case.decision_time
            if case.decision_time is not None
            else max((event.timestamp for event in case.events), default=None)
        )
        closure = (
            policies.closure.evaluate(
                case.closure_proposal, case_store, config, as_of=decision_time
            )
            if case.closure_proposal
            else None
        )
        reopened = closure
        reopen_signals = []
        if closure is not None:
            reopened, reopen_signals = check_reopen(
                closure, case.events, effective_time=decision_time
            )

        provider = make_provider(config)
        config_hash = sha256_json(
            {
                "context_budget_tokens": config.context_budget_tokens,
                "rrf_k": config.rrf_k,
                "candidate_top_n": config.candidate_top_n,
                "provider_kind": config.provider_kind,
                "provider_model": config.provider_model,
                "temperature": config.temperature,
                "max_output_tokens": config.max_output_tokens,
            }
        )
        arm_results: dict[str, Any] = {}
        for arm in arms:
            retrieval = policies.retrieval.execute(case_store, case.query, arm, config)
            context = assemble_context(
                case_store, case.query, retrieval, config, scaffold=scaffold
            )
            response = provider.generate(context)
            metrics = _metrics(
                case,
                arm=arm,
                retrieval_selected=set(context.selected_event_ids),
                response_citations=set(response.cited_event_ids),
                transfer=transfer,
                closure=reopened,
                projection_stable=projection_stable,
                response_answer=response.answer,
            )
            run_log = RunLog(
                run_id=new_id("run"),
                arm=arm,
                case_id=case.case_id,
                model=response.model,
                params={
                    "query": case.query,
                    "scaffold": ReasoningMode(scaffold).value,
                    "candidate_pool_size": len(retrieval.candidate_pool_ids),
                    "retrieval_channels": sorted({item.channel for item in retrieval.selected}),
                    "actor_cursor_manifest": context.actor_cursor_manifest,
                    "policy_bundle": {
                        "retrieval": type(policies.retrieval).__name__,
                        "transfer": type(policies.transfer).__name__,
                        "closure": type(policies.closure).__name__,
                        "problem_link": type(policies.problem_link).__name__,
                        "runtime_resolution": (
                            policy_runtime.manifest() if policy_runtime is not None else None
                        ),
                    },
                },
                config_hash=config_hash,
                context_hash=context.context_hash,
                output_hash=sha256_text(response.answer),
                input_tokens=context.input_tokens,
                output_tokens=response.output_tokens,
                selected_event_ids=list(context.selected_event_ids),
                sent_to_provider=context.sent_to_provider,
                sent_event_ids=list(context.sent_event_ids),
                provider_manifest=dict(context.provider_manifest),
                answer=redact_secrets(response.answer) if config.redact_secrets else response.answer,
                metrics=metrics,
            )
            if log_store is not None:
                log_store.save_run_log(run_log.model_dump(mode="json"))
            arm_results[arm] = {
                "retrieval": retrieval.model_dump(mode="json"),
                "context": {
                    "selected_event_ids": context.selected_event_ids,
                    "input_tokens": context.input_tokens,
                    "context_hash": context.context_hash,
                    "actor_cursors": [
                        cursor.model_dump(mode="json") for cursor in context.actor_cursors
                    ],
                    "actor_cursor_manifest": context.actor_cursor_manifest,
                    "provider_manifest": context.provider_manifest,
                },
                "response": response.model_dump(mode="json"),
                "metrics": metrics,
                "run_id": run_log.run_id,
                "run_log": run_log.model_dump(mode="json"),
            }
        case_store.close()

    return {
        "case_id": case.case_id,
        "title": case.title,
        "query": case.query,
        "projection_hash": first_hash,
        "raw_history_hash": raw_history_hash,
        "projection_stable": projection_stable,
        "transfer": transfer.model_dump(mode="json") if transfer else None,
        "closure": reopened.model_dump(mode="json") if reopened else None,
        "reopen_signals": [signal.model_dump(mode="json") for signal in reopen_signals],
        "scaffold": ReasoningMode(scaffold).value,
        "arms": arm_results,
    }


def _projection_hash(store: Store) -> str:
    return sha256_json(store.projection_payload())


def run_suite(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    output_json: str | Path = "docs/EVAL_RESULTS.json",
    output_markdown: str | Path = "docs/EVAL_REPORT.md",
    arms: tuple[str, ...] = ARMS,
    policy_bundle: PolicyBundle | None = None,
) -> dict[str, Any]:
    normalized_arms = _normalize_arms(arms)
    cases = load_fixtures(fixture_dir)
    log_store = Store(config.data_dir / "eval_runs.sqlite3")
    log_store.init_schema()
    results = [
        run_case(
            case,
            config,
            log_store=log_store,
            arms=normalized_arms,
            policy_bundle=policy_bundle,
        )
        for case in cases
    ]
    gates = _gates(results, arms=normalized_arms)
    report = {
        "schema_version": "eval.v0.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "context_budget_tokens": config.context_budget_tokens,
            "rrf_k": config.rrf_k,
            "candidate_top_n": config.candidate_top_n,
            "provider_kind": config.provider_kind,
            "provider_model": config.provider_model,
        },
        "requested_arms": list(normalized_arms),
        "executed_arms": list(normalized_arms),
        "cases": results,
        "gates": gates,
    }
    json_path = Path(output_json)
    md_path = Path(output_markdown)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    log_store.close()
    return report


def _metrics(
    case: EvalCase,
    *,
    arm: str,
    retrieval_selected: set[str],
    response_citations: set[str],
    transfer,
    closure,
    projection_stable: bool,
    response_answer: str = "",
) -> dict[str, float | str]:
    expected = case.expected
    required = set(expected.get("required_event_ids", []))
    dissent = set(expected.get("dissent_event_ids", []))
    temporal = set(expected.get("temporal_event_ids", []))
    allowed = set(expected.get("allowed_event_ids", []))
    selected = retrieval_selected
    result: dict[str, float] = {
        "provider_citation_coverage": _coverage(response_citations, selected),
        # ASSEMBLER-SIDE ONLY (epoch 4, F-0). `response_citations` is built by filtering the
        # already-selected set (providers.py: `cited = [id for id in selected if id in answer]`),
        # so `response_citations - selected` is empty BY CONSTRUCTION and this value cannot fail.
        # It is kept as bookkeeping, NOT as evidence about the model. The falsifiable measure at
        # the model-output level is `unsupported_reference_rate`, computed from the raw answer.
        # The empty branch is UNSCORED rather than 0.0 (F-2): an answer that cites nothing has not
        # demonstrated clean citation behaviour.
        "unsupported_claim_rate": (
            len(response_citations - selected) / len(response_citations)
            if response_citations
            else UNSCORED
        ),
        # F-2: nothing checkable is not the same as nothing wrong.
        "problem_contamination_rate": (
            len(selected - allowed) / len(selected) if allowed and selected else UNSCORED
        ),
        # F-0: measured from the raw answer, so it can fail. UNSCORED when the answer names
        # no identifiers - which is deliberately not treated as a clean result.
        "unsupported_reference_rate": unsupported_reference_rate(response_answer, selected),
        "projection_rebuild_stable": 1.0 if projection_stable else 0.0,
    }
    if required:
        result["provenance_coverage"] = _coverage(required, selected)
    if dissent:
        result["dissent_preservation_rate"] = _coverage(dissent, selected)
    if temporal:
        result["temporal_revision_accuracy"] = _coverage(temporal, selected)
    all_actors = {event.actor_id for event in case.events}
    selected_actors = {event.actor_id for event in case.events if event.id in selected}
    if all_actors:
        result["actor_coverage"] = len(all_actors & selected_actors) / len(all_actors)
    expected_transfer = expected.get("transfer_decision")
    if expected_transfer:
        actual_transfer = str(transfer.decision) if transfer is not None else "NOT_RUN"
        result["negative_transfer_rejection"] = (
            1.0 if actual_transfer == expected_transfer else 0.0
        )
        expected_positive = expected_transfer == "CANDIDATE"
        actual_positive = actual_transfer == "CANDIDATE"
        result["transfer_true_positive"] = float(expected_positive and actual_positive)
        result["transfer_false_positive"] = float(not expected_positive and actual_positive)
        result["transfer_false_negative"] = float(expected_positive and not actual_positive)
    expected_closure = expected.get("closure_status")
    if expected_closure:
        result["closure_gate_correct"] = (
            1.0 if closure is not None and str(closure.status) == expected_closure else 0.0
        )
        result["false_closure_rate"] = (
            1.0 if expected_closure == "NO_CLOSURE" and closure is not None and str(closure.status) == "ACTIVE" else 0.0
        )
        result["false_no_closure_rate"] = (
            1.0
            if expected_closure == "ACTIVE"
            and closure is not None
            and str(closure.status) == "NO_CLOSURE"
            else 0.0
        )
        result["team_level_overreach"] = (
            1.0
            if closure is not None
            and str(closure.level) == "TEAM"
            and str(closure.status) == "ACTIVE"
            and expected_closure == "NO_CLOSURE"
            else 0.0
        )
    expected_reopen = expected.get("reopen_status")
    if expected_reopen:
        result["reopen_correctness"] = (
            1.0 if closure is not None and str(closure.status) == expected_reopen else 0.0
        )
    # BASE is intentionally allowed to have no evidence; the metric records
    # the absence rather than silently treating it as a successful retrieval.
    result["arm_has_retrieved_evidence"] = 1.0 if selected else 0.0
    return {
        key: (round(value, 6) if isinstance(value, float) else value)
        for key, value in result.items()
    }


# Epoch 4 (F-2): an empty denominator is NOT a perfect score. It is not a zero score either.
# It is a statement that nothing was measured, and it must be unable to render as a number.
# Old epoch-1/2/3 artifacts keep their recorded values; this changes only future runs.
# The sentinel itself lives in contracts.py so every measurement surface shares one definition.


def _coverage(expected: set[str], actual: set[str]) -> float | str:
    if not expected:
        return UNSCORED
    return len(expected.intersection(actual)) / len(expected)


def scored_unscored_counts(metrics: dict[str, object]) -> dict[str, int]:
    """How many values in a metric vector are measurements, and how many are not."""

    scored = sum(1 for value in metrics.values() if isinstance(value, int | float))
    return {"scored": scored, "unscored": len(metrics) - scored}


def citation_binding_integrity(cited_ids: set[str], pool_ids: set[str]) -> bool:
    """Assembler-managed structural invariant: every cited id came from the candidate pool.

    Epoch 4, F-0. This checks the HARNESS bookkeeping, not the model output: the adapter
    builds the cited set by filtering the already-selected set, so a producer that respects
    that construction can never make this check fail. It is therefore reported as a
    structural invariant and NOT as independent evidence about model provenance.
    """

    return set(cited_ids).issubset(set(pool_ids))


def unsupported_reference_rate(answer: str, sent_event_ids: set[str]) -> float | str:
    """Model-output-level check: event ids the answer names that it was never given (F-0).

    This is computed from the RAW answer text, so unlike the assembler-side invariant it can
    genuinely fail. Returns UNSCORED when the answer names no identifiers at all - an answer
    that cites nothing is not a clean answer, it is an unmeasured one.
    """

    from .reasoning import cited_event_ids

    named = set(cited_event_ids(answer))
    if not named:
        return UNSCORED
    return len(named - set(sent_event_ids)) / len(named)


def _normalize_arms(arms: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
    raw = arms.split(",") if isinstance(arms, str) else list(arms)
    normalized = tuple(dict.fromkeys(item.strip().upper() for item in raw if item.strip()))
    invalid = sorted(set(normalized).difference(ARMS))
    if not normalized:
        raise ValueError("at least one evaluation arm is required")
    if invalid:
        raise ValueError(f"unsupported evaluation arms: {', '.join(invalid)}")
    return normalized


def _gates(results: list[dict[str, Any]], *, arms: tuple[str, ...] = ARMS) -> dict[str, Any]:
    by_case = {result["case_id"]: result for result in results}
    c04 = by_case.get("C04")
    c05 = by_case.get("C05")
    dissent_cases = [result for result in results if result["case_id"] in {"C02", "C03"}]
    protected_dissent = (
        all(
            result["arms"]["SOUL"]["metrics"].get("dissent_preservation_rate", 0.0) >= 1.0
            for result in dissent_cases
        )
        if dissent_cases and "SOUL" in arms
        else "NOT_APPLICABLE"
    )
    if "SOUL" not in arms:
        protected_dissent = "NOT_OBSERVED"
    # Epoch 4, F-0: renamed from `provenance_hallucination_zero`, which claimed more than it
    # measured. This is the assembler-side invariant and it cannot fail under the current
    # adapter; the falsifiable, model-output-level counterpart is `unsupported_reference_rate`.
    no_unproven_citations = all(
        citation_binding_integrity(
            set(arm["response"]["cited_event_ids"]), set(arm["retrieval"]["candidate_pool_ids"])
        )
        for result in results
        for arm in result["arms"].values()
    )
    return {
        "citation_binding_integrity": no_unproven_citations,
        "protected_dissent_retained": protected_dissent,
        "hard_negative_transfer_reject": (
            bool(c04 and c04["transfer"] and c04["transfer"]["decision"] == "REJECT")
            if c04 is not None
            else "NOT_APPLICABLE"
        ),
        "forbidden_team_closure_zero": (
            bool(c05["closure"] is None or c05["closure"]["status"] != "ACTIVE")
            if c05 is not None
            else "NOT_APPLICABLE"
        ),
        "expected_no_closure_accepted": (
            bool(c05["closure"] is not None and c05["closure"]["status"] == "NO_CLOSURE")
            if c05 is not None
            else "NOT_APPLICABLE"
        ),
        "projection_hash_stable": all(result["projection_stable"] for result in results),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.1 Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Executed arms: `{', '.join(report.get('executed_arms', []))}`",
        "",
        "## Gates",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for name, result in report["gates"].items():
        label = "PASS" if result is True else "FAIL" if result is False else "N/A"
        lines.append(f"| {name} | {label} |")
    lines.extend(["", "## Cases", ""])
    for case in report["cases"]:
        lines.extend([f"### {case['case_id']} — {case['title']}", ""])
        if case["transfer"]:
            lines.append(
                f"Transfer decision: `{case['transfer']['decision']}` ({', '.join(case['transfer']['reason_codes'])})"
            )
        if case["closure"]:
            lines.append(
                f"Closure status: `{case['closure']['status']}` ({', '.join(case['closure']['reason_codes']) or 'no blocking reason'})"
            )
        lines.append("")
        lines.append("| Arm | Selected events | Key metrics |")
        lines.append("|---|---:|---|")
        for arm_name, arm in case["arms"].items():
            metrics = ", ".join(
                f"{key}={value}" for key, value in arm["metrics"].items() if key in {
                    "provenance_coverage",
                    "dissent_preservation_rate",
                    "temporal_revision_accuracy",
                    "negative_transfer_rejection",
                    "closure_gate_correct",
                    "reopen_correctness",
                }
            )
            lines.append(
                f"| {arm_name} | {len(arm['retrieval']['selected'])} | {metrics or 'n/a'} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "This report provides structural fixture evidence only. It does not establish real-world team benefit, model superiority outside the declared corpus, or the existence of a team-level Soul.",
            "",
        ]
    )
    return "\n".join(lines)
