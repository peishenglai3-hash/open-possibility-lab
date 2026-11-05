import json
from datetime import datetime, timezone
from pathlib import Path

from team_soul.config import RuntimeConfig
from team_soul.context import assemble_context
from team_soul.eval import run_case, run_suite
from team_soul.ingest import load_fixture
from team_soul.projection import rebuild_projection
from team_soul.retrieval import retrieve
from team_soul.storage import Store
from team_soul.contracts import RetrievalResult
from team_soul.kernel import PolicyBundle, deterministic_policy_bundle


class _EmptyRetrieval:
    def execute(self, store, query, arm, config):
        return RetrievalResult(
            arm=arm,
            query=query,
            budget_tokens=config.context_budget_tokens,
        )


def test_soul_retrieval_preserves_dissent_and_budget(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C03_dissent_preservation.json"))
    store = Store(tmp_path / "case.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    rebuild_projection(store)
    result = retrieve(store, case.query, "SOUL", RuntimeConfig(context_budget_tokens=100))
    selected = {item.event_id for item in result.selected}
    assert "evt_c03_2" in selected
    assert result.context_tokens <= result.budget_tokens
    context = assemble_context(
        store,
        case.query,
        result,
        RuntimeConfig(context_budget_tokens=100),
    )
    assert context.input_tokens <= 100
    store.close()


def test_as_of_retrieval_and_context_exclude_future_events_and_carry_actor_cursors(
    tmp_path: Path,
) -> None:
    case = load_fixture(Path("fixtures/C01_historical_revision.json"))
    store = Store(tmp_path / "as-of.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    rebuild_projection(store)
    as_of = datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)
    config = RuntimeConfig(context_budget_tokens=300)

    retrieval = retrieve(store, "batch size", "SOUL", config, as_of=as_of)
    selected = {item.event_id for item in retrieval.selected}
    assert selected == {"evt_c01_1"}
    context = assemble_context(store, case.query, retrieval, config, as_of=as_of)
    assert set(context.selected_event_ids) == {"evt_c01_1"}
    assert context.actor_cursor_manifest["as_of"] == as_of.isoformat()
    assert context.actor_cursor_manifest["actor_ids"] == ["alice"]
    assert all(
        event_id == "evt_c01_1"
        for cursor in context.actor_cursors
        for event_id in cursor.observed_event_ids
    )
    store.close()


def test_evaluation_uses_injected_policy_bundle_without_mutating_raw_history() -> None:
    case = load_fixture(Path("fixtures/C03_dissent_preservation.json"))
    default_bundle = deterministic_policy_bundle()
    replaced_bundle = PolicyBundle(
        retrieval=_EmptyRetrieval(),
        transfer=default_bundle.transfer,
        closure=default_bundle.closure,
        problem_link=default_bundle.problem_link,
        activation=default_bundle.activation,
    )

    baseline = run_case(case, RuntimeConfig(), arms=("SOUL",), policy_bundle=default_bundle)
    replaced = run_case(case, RuntimeConfig(), arms=("SOUL",), policy_bundle=replaced_bundle)

    assert baseline["raw_history_hash"] == replaced["raw_history_hash"]
    assert baseline["projection_hash"] == replaced["projection_hash"]
    assert baseline["arms"]["SOUL"]["context"]["selected_event_ids"]
    assert replaced["arms"]["SOUL"]["context"]["selected_event_ids"] == []
    assert replaced["arms"]["SOUL"]["run_log"]["params"]["policy_bundle"]["retrieval"] == "_EmptyRetrieval"


def test_public_suite_gates_and_machine_report(tmp_path: Path) -> None:
    report = run_suite(
        "fixtures",
        RuntimeConfig(data_dir=tmp_path / "runtime"),
        output_json=tmp_path / "EVAL_RESULTS.json",
        output_markdown=tmp_path / "EVAL_REPORT.md",
    )
    assert all(report["gates"].values())
    assert {case["case_id"] for case in report["cases"]} == {"C01", "C02", "C03", "C04", "C05"}
    loaded = json.loads((tmp_path / "EVAL_RESULTS.json").read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "eval.v0.1"
    assert "NO_CLOSURE" in (tmp_path / "EVAL_REPORT.md").read_text(encoding="utf-8")


def test_eval_arms_control_execution_and_manifest(tmp_path: Path) -> None:
    report = run_suite(
        "fixtures",
        RuntimeConfig(data_dir=tmp_path / "runtime-arms"),
        output_json=tmp_path / "ARMS.json",
        output_markdown=tmp_path / "ARMS.md",
        arms=("BASE",),
    )
    assert report["requested_arms"] == ["BASE"]
    assert report["executed_arms"] == ["BASE"]
    assert all(set(case["arms"]) == {"BASE"} for case in report["cases"])
    assert report["gates"]["protected_dissent_retained"] == "NOT_OBSERVED"
