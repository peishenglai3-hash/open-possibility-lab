from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .config import RuntimeConfig, load_config
from .context import assemble_context
from .demo_runtime import run_demo_inspection
from .contracts import ClosureProposal, OperationalClosure, TransferCandidate
from .eval import run_suite
from .eval_v02 import run_v02_suite
from .eval_d6 import run_d6_suite
from .eval_d7 import run_d7_suite
from .eval_promotion import run_promotion_dry_run
from .ingest import append_json_event, ingest_path, load_fixture
from .kernel.policies import policy_catalog_markdown
from .pilot import (
    PilotFeedback,
    append_pilot_feedback,
    preview_pilot,
    run_pilot_d8_suite,
)
from .release_report import run_d9_bundle
from .operators import (
    check_transfer,
    persist_closure_proposal,
    persist_reopen,
    revoke_closure,
    validate_closure,
)
from .projection import actor_cursor, distributed_view, load_closures, problem_event_index, rebuild_projection
from .providers import make_provider
from .retrieval import retrieve
from .storage import Store


app = typer.Typer(help="Team Soul short-cycle demo runtime")
event_app = typer.Typer(help="Append or inspect raw events")
eval_app = typer.Typer(help="Run the fixture evaluation suite")
transfer_app = typer.Typer(help="Guarded cross-project transfer")
closure_app = typer.Typer(help="Scoped operational closure")
reopen_app = typer.Typer(help="Revocable closure checks")
pilot_app = typer.Typer(help="Suggestion-only pilot preview and human review")
app.add_typer(event_app, name="event")
app.add_typer(eval_app, name="eval")
app.add_typer(transfer_app, name="transfer")
app.add_typer(closure_app, name="closure")
app.add_typer(reopen_app, name="reopen")
app.add_typer(pilot_app, name="pilot")


def _config(config: Optional[Path]) -> RuntimeConfig:
    return load_config(config)


def _parse_as_of(value: Optional[str]) -> Optional[datetime]:
    """Parse a CLI --as-of bound, assuming UTC when no offset is given (FR-13)."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid --as-of value: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _store(config: Optional[Path]) -> Store:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    store.init_schema()
    return store


@app.command("init")
def init(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    store.init_schema()
    store.close()
    typer.echo(f"initialized {runtime.sqlite_path}")


@app.command("ingest")
def ingest(
    path: Path,
    private: bool = typer.Option(False, "--private", help="Store only opaque private source locators"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    ids = ingest_path(store, path, private=private)
    projection_hash = rebuild_projection(store)
    store.close()
    typer.echo(json.dumps({"ingested": len(ids), "event_ids": ids, "projection_hash": projection_hash}, ensure_ascii=False))


@event_app.command("append")
def event_append(
    payload: str = typer.Argument(..., help="JSON event payload or path to a JSON file"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    candidate_path = Path(payload) if not payload.lstrip().startswith("{") else None
    raw = candidate_path.read_text(encoding="utf-8") if candidate_path and candidate_path.is_file() else payload
    event_id = append_json_event(store, raw)
    projection_hash = rebuild_projection(store)
    store.close()
    typer.echo(json.dumps({"event_id": event_id, "projection_hash": projection_hash}))


@event_app.command("list")
def event_list(
    limit: Optional[int] = typer.Option(None, "--limit"),
    problem: Optional[str] = typer.Option(None, "--problem"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    store = _store(config)
    events = store.list_events(limit)
    if problem:
        allowed = set(problem_event_index(store).get(problem, []))
        events = [event for event in events if event.id in allowed]
    typer.echo(json.dumps([event.model_dump(mode="json") for event in events], ensure_ascii=False, indent=2))
    store.close()


@app.command("rebuild")
def rebuild(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    store = _store(config)
    projection_hash = rebuild_projection(store)
    store.close()
    typer.echo(json.dumps({"projection_hash": projection_hash}))


@app.command("project")
def project(
    event_ids: list[str] = typer.Argument(None),
    query: Optional[str] = typer.Option(None, "--query"),
    time: Optional[str] = typer.Option(None, "--time", help="ISO date/time lower bound"),
    problem: Optional[str] = typer.Option(None, "--problem"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    selected = set(event_ids or [])
    retrieval_payload = None
    if query:
        retrieval_result = retrieve(store, query, "SOUL", runtime)
        selected.update(item.event_id for item in retrieval_result.selected)
        retrieval_payload = retrieval_result.model_dump(mode="json")
    if problem:
        selected.update(problem_event_index(store).get(problem, []))
    if time:
        selected = {
            event.id
            for event in store.list_events()
            if event.id in selected and event.timestamp.isoformat() >= time
        }
    payload = distributed_view(store, sorted(selected))
    if retrieval_payload is not None:
        payload["retrieval"] = retrieval_payload
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    store.close()


@app.command("retrieve")
def retrieve_command(
    query: str,
    arm: str = typer.Option("SOUL", "--arm"),
    budget: Optional[int] = typer.Option(None, "--budget"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    if budget is not None:
        runtime = replace(runtime, context_budget_tokens=budget)
    store = Store(runtime.sqlite_path)
    result = retrieve(store, query, arm, runtime)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


@transfer_app.command("check")
def transfer_check(
    candidate_json: Optional[Path] = typer.Option(None, "--candidate-json"),
    source: str = typer.Option("", "--source"),
    target: str = typer.Option("", "--target"),
) -> None:
    if candidate_json:
        candidate = TransferCandidate.model_validate(json.loads(candidate_json.read_text(encoding="utf-8")))
    else:
        candidate = TransferCandidate(source_case_id=source, target_case_id=target)
    typer.echo(json.dumps(check_transfer(candidate).model_dump(mode="json"), ensure_ascii=False, indent=2))


@closure_app.command("propose")
def closure_propose(
    proposal_json: Path,
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    proposal = ClosureProposal.model_validate(json.loads(proposal_json.read_text(encoding="utf-8")))
    result = persist_closure_proposal(proposal, store, runtime)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


@closure_app.command("validate")
def closure_validate(
    proposal_json: Path,
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = _store(config)
    proposal = ClosureProposal.model_validate(json.loads(proposal_json.read_text(encoding="utf-8")))
    result = validate_closure(proposal, store, runtime)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


@closure_app.command("inspect")
def closure_inspect(
    closure_id: str,
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    store = _store(config)
    rebuild_projection(store)
    closures = [closure for closure in load_closures(store) if closure.id == closure_id]
    if not closures:
        raise typer.BadParameter(f"closure not found: {closure_id}")
    typer.echo(json.dumps(closures[0].model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


@reopen_app.command("check")
def reopen_check_command(
    closure_id: str,
    event: list[str] = typer.Option([], "--event"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    store = _store(config)
    rebuild_projection(store)
    closures = [closure for closure in load_closures(store) if closure.id == closure_id]
    if not closures:
        raise typer.BadParameter(f"closure not found: {closure_id}")
    events = [store.get_event(event_id) for event_id in event] if event else store.list_events()
    trigger_events = [item for item in events if item is not None]
    # G0-A: the reopen scan is bounded by a frozen decision time (ADR U1); the latest
    # known record is the strongest bound available without a RuntimeContext here.
    decision_time = max((item.timestamp for item in trigger_events), default=None)
    if decision_time is None:
        raise typer.BadParameter(
            "reopen needs at least one raw event to establish the decision time"
        )
    result, signals = persist_reopen(
        closure_id,
        store,
        trigger_events,
        effective_time=decision_time,
    )
    typer.echo(
        json.dumps(
            {
                "closure": result.model_dump(mode="json"),
                "signals": [signal.model_dump(mode="json") for signal in signals],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    store.close()


@closure_app.command("revoke")
def closure_revoke(
    closure_id: str,
    reason: str = typer.Option("revoked by human review", "--reason"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    store = _store(config)
    result = revoke_closure(store, closure_id, reason)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


@eval_app.command("suite")
def eval_suite(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    output_json: Path = typer.Option(Path("docs/EVAL_RESULTS.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/EVAL_REPORT.md"), "--output-markdown"),
    arms: str = typer.Option("base,rag,soul", "--arms"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = run_suite(
        fixture_dir,
        runtime,
        output_json=output_json,
        output_markdown=output_markdown,
        arms=tuple(item.strip() for item in arms.split(",") if item.strip()),
    )
    typer.echo(json.dumps(report["gates"], ensure_ascii=False, indent=2))


@eval_app.command("run")
def eval_run(
    case: Optional[str] = typer.Option(None, "--case"),
    arms: str = typer.Option("base,rag,soul", "--arms"),
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    case_json: Optional[Path] = typer.Argument(None),
    output_json: Path = typer.Option(Path("docs/EVAL_RESULTS.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/EVAL_REPORT.md"), "--output-markdown"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    import tempfile

    runtime = _config(config)
    source_case = case_json
    if source_case is None and case:
        matches = sorted(fixture_dir.glob(f"{case}*.json"))
        if not matches:
            raise typer.BadParameter(f"fixture case not found: {case}")
        source_case = matches[0]
    if source_case is None:
        raise typer.BadParameter("provide --case C01 or a fixture JSON path")
    with tempfile.TemporaryDirectory(prefix="team-soul-single-eval-") as directory:
        source = Path(directory) / source_case.name
        source.write_text(source_case.read_text(encoding="utf-8"), encoding="utf-8")
        report = run_suite(
            Path(directory),
            runtime,
            output_json=output_json,
            output_markdown=output_markdown,
            arms=tuple(item.strip() for item in arms.split(",") if item.strip()),
        )
    typer.echo(json.dumps(report["gates"], ensure_ascii=False, indent=2))


@eval_app.command("v02")
def eval_v02(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    historical_dir: Optional[Path] = typer.Option(None, "--historical-dir"),
    output_json: Path = typer.Option(Path("docs/EVAL_RESULTS_V02.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/EVAL_REPORT_V02.md"), "--output-markdown"),
    output_manifest: Path = typer.Option(Path("docs/SCENARIO_MATRIX_V02.json"), "--output-manifest"),
    arms: str = typer.Option("base,rag,soul", "--arms"),
    scenario_limit: Optional[int] = typer.Option(None, "--scenario-limit"),
    ablations: bool = typer.Option(True, "--ablations/--no-ablations"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = run_v02_suite(
        fixture_dir,
        runtime,
        historical_dir=historical_dir,
        output_json=output_json,
        output_markdown=output_markdown,
        output_manifest=output_manifest,
        arms=tuple(item.strip() for item in arms.split(",") if item.strip()),
        scenario_limit=scenario_limit,
        run_ablations=ablations,
    )
    typer.echo(json.dumps(report["gates"], ensure_ascii=False, indent=2))


@eval_app.command("d6")
def eval_d6(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    d5_report: Path = typer.Option(Path("docs/EVAL_RESULTS_V02.json"), "--d5-report"),
    output_json: Path = typer.Option(Path("docs/FAILURE_REGISTER.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/FAILURE_REGISTER.md"), "--output-markdown"),
    failure_budget: int = typer.Option(20, "--failure-budget"),
    heldout_ratio: float = typer.Option(0.25, "--heldout-ratio"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = run_d6_suite(
        fixture_dir,
        runtime,
        d5_report=d5_report,
        output_json=output_json,
        output_markdown=output_markdown,
        failure_budget=failure_budget,
        heldout_ratio=heldout_ratio,
    )
    typer.echo(json.dumps(report["gates"], ensure_ascii=False, indent=2))


@eval_app.command("d7")
def eval_d7(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    d5_report: Path = typer.Option(Path("docs/EVAL_RESULTS_V02.json"), "--d5-report"),
    d6_report: Path = typer.Option(Path("docs/FAILURE_REGISTER.json"), "--d6-report"),
    output_json: Path = typer.Option(Path("docs/MULTI_MODEL_PARTIAL_REPORT.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/EVAL_REPORT_D7.md"), "--output-markdown"),
    reasoning_output: Path = typer.Option(Path("docs/REASONING_ABLATION_V02.json"), "--reasoning-output"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = run_d7_suite(
        fixture_dir,
        runtime,
        d5_report=d5_report,
        d6_report=d6_report,
        output_json=output_json,
        output_markdown=output_markdown,
        reasoning_output=reasoning_output,
    )
    typer.echo(json.dumps(report["gates"], ensure_ascii=False, indent=2))


@eval_app.command("promotion-dry-run")
def eval_promotion_dry_run(
    d5_report: Path = typer.Option(Path("docs/EVAL_RESULTS_V02.json"), "--d5-report"),
    d6_report: Path = typer.Option(Path("docs/FAILURE_REGISTER.json"), "--d6-report"),
    output_json: Path = typer.Option(Path("docs/PROMOTION_DRY_RUN_D9.json"), "--output-json"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    report = run_promotion_dry_run(d5_report, d6_report, _config(config))
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.echo(
        json.dumps(
            {
                "decision": report["promotion_decision"]["decision"],
                "reason_codes": report["promotion_decision"]["reason_codes"],
                "auto_promote": report["auto_promote"],
                "candidate_activated": report["candidate_activated"],
                "raw_history_unchanged": report["raw_history_unchanged"],
                "output": str(output_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@eval_app.command("d9-bundle")
def eval_d9_bundle(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    allow_configured_provider: bool = typer.Option(
        False,
        "--allow-configured-provider",
        help="Required for a configured non-mock provider; the full D5 matrix includes hundreds of calls.",
    ),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    result = run_d9_bundle(
        fixture_dir,
        _config(config),
        allow_configured_provider=allow_configured_provider,
    )
    report = result["report"]
    typer.echo(
        json.dumps(
            {
                "overall_gate": report["overall_gate"],
                "unresolved_or_failed_gates": report["unresolved_or_failed_gates"],
                "tests": report["test_run"]["summary"],
                "scenario_manifest_hash": report["projection_and_replay_hashes"]["scenario_manifest_hash"],
                "run_manifest": result["run_manifest"]["schema_version"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("policy-catalog")
def policy_catalog(
    output_markdown: Path = typer.Option(Path("docs/POLICY_REGISTRY.md"), "--output-markdown"),
) -> None:
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(policy_catalog_markdown(), encoding="utf-8")
    typer.echo(str(output_markdown))


@pilot_app.command("preview")
def pilot_preview_command(
    case_file: Path = typer.Option(..., "--case-file", exists=True, dir_okay=False),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = preview_pilot(load_fixture(case_file), runtime)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@pilot_app.command("record-review")
def pilot_record_review(
    review_json: Path = typer.Argument(..., exists=True, dir_okay=False),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    feedback = PilotFeedback.model_validate(json.loads(review_json.read_text(encoding="utf-8")))
    store = Store(runtime.sqlite_path)
    store.init_schema()
    raw_history_before = [event.id for event in store.list_events()]
    feedback_id = append_pilot_feedback(store, feedback)
    raw_history_after = [event.id for event in store.list_events()]
    store.close()
    typer.echo(
        json.dumps(
            {
                "feedback_id": feedback_id,
                "rating": feedback.rating,
                "evidence_status": (
                    "SELF_ATTESTED_E4_PENDING_AUDIT"
                    if feedback.live_use_confirmed
                    else "UNVERIFIED"
                ),
                "raw_history_unchanged": raw_history_before == raw_history_after,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@pilot_app.command("suite")
def pilot_suite(
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    output_json: Path = typer.Option(Path("docs/PILOT_NOTES_D8.json"), "--output-json"),
    output_markdown: Path = typer.Option(Path("docs/PILOT_NOTES.md"), "--output-markdown"),
    output_steering_json: Path = typer.Option(Path("docs/STEERING_AUDIT_D8.json"), "--output-steering-json"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    report = run_pilot_d8_suite(
        fixture_dir,
        runtime,
        output_json=output_json,
        output_markdown=output_markdown,
        output_steering_json=output_steering_json,
    )
    typer.echo(
        json.dumps(
            {
                "all_suggestions_proposals": report["all_suggestions_proposals"],
                "raw_history_unchanged": report["raw_history_unchanged"],
                "human_feedback": report["human_feedback"]["status"],
                "real_failure_repair": report["real_failure_repair"]["status"],
                "e4_live_pilot": report["e4_live_pilot"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("demo")
def demo(
    query: str = typer.Option("What should the team decide next?", "--query"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    store = Store(runtime.sqlite_path)
    provider = make_provider(runtime)
    output = {}
    for arm in ("BASE", "RAG", "SOUL"):
        retrieval_result = retrieve(store, query, arm, runtime)
        context = assemble_context(store, query, retrieval_result, runtime)
        output[arm] = {
            "selected_event_ids": context.selected_event_ids,
            "context_hash": context.context_hash,
            "response": provider.generate(context).model_dump(mode="json"),
        }
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2))
    store.close()


@app.command("demo-inspect")
def demo_inspect(
    case_id: str = typer.Option("C01", "--case"),
    fixture_dir: Path = typer.Option(Path("fixtures"), "--fixture-dir"),
    failure_report: Path = typer.Option(Path("docs/FAILURE_REGISTER.json"), "--failure-report"),
    output_json: Path = typer.Option(Path("docs/DEMO_INSPECTION_V02.json"), "--output-json"),
    allow_configured_provider: bool = typer.Option(
        False,
        "--allow-configured-provider",
        help="Required when the selected config uses a non-mock provider; one run includes all ablations.",
    ),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    runtime = _config(config)
    if runtime.provider_kind != "mock" and not allow_configured_provider:
        raise typer.BadParameter(
            "non-mock configuration requires explicit --allow-configured-provider; "
            "inspection runs BASE/RAG/SOUL and five ablations"
        )
    matches = sorted(fixture_dir.glob(f"{case_id}*.json"))
    if not matches:
        raise typer.BadParameter(f"fixture case not found: {case_id}")
    report = run_demo_inspection(
        load_fixture(matches[0]), runtime, failure_report_path=failure_report
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.echo(
        json.dumps(
            {
                "experiment_hash": report["experiment_hash"],
                "case_id": report["case_id"],
                "arms": list(report["arms"]),
                "ablations": list(report["ablations"]),
                "failure_candidates": report["failure_scout"].get("candidate_count_for_case", 0),
                "output": str(output_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@event_app.command("cursor")
def event_cursor(
    actor_id: str,
    as_of: Optional[str] = typer.Option(None, "--as-of", help="ISO timestamp upper bound"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    store = _store(config)
    result = actor_cursor(store, actor_id, as_of=_parse_as_of(as_of))
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    store.close()


if __name__ == "__main__":
    app()
