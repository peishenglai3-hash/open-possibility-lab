from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from team_soul.cli import app
from team_soul.demo_runtime import run_demo_inspection
from team_soul.config import RuntimeConfig
from team_soul.ingest import load_fixture


def test_cli_and_shared_demo_runtime_emit_the_same_reproducible_core(tmp_path) -> None:
    if not Path("docs/FAILURE_REGISTER.json").is_file():
        pytest.skip("the Week02 failure register is not distributed in the public snapshot")
    case = load_fixture("fixtures/C01_historical_revision.json")
    expected = run_demo_inspection(case, RuntimeConfig())
    output = tmp_path / "demo-inspection.json"

    result = CliRunner().invoke(
        app,
        ["demo-inspect", "--case", "C01", "--output-json", str(output)],
    )

    assert result.exit_code == 0, result.output
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["experiment_hash"] == expected["experiment_hash"]
    assert set(saved["arms"]) == {"BASE", "RAG", "SOUL"}
    assert set(saved["ablations"]) == {
        "no-dissent", "no-temporal", "no-transfer", "no-closure", "no-problem"
    }
    assert saved["failure_scout"]["status"] != "NOT OBSERVED / OPEN GAP"


def test_demo_runtime_never_mislabels_fixture_as_live_pilot() -> None:
    report = run_demo_inspection(
        load_fixture("fixtures/C05_no_closure_own_model.json"),
        RuntimeConfig(),
    )

    assert report["evidence_level"] == "E1"
    assert report["mode"] == "FIXTURE_EXPERIMENT_ONLY"
    assert "not E2/E4" in report["interpretation_boundary"]
