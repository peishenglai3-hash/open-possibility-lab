"""Week03 core tests: composer determinism, export allowlist, authority firewall, launcher."""

from __future__ import annotations

import json
import sys

import pytest

from w03.composer import EXPANSIONS, compose, expansion, reject_question, refusal_response
from w03.export import ALLOWED_KEYS, build_payload, export_json, export_markdown, finalize
from w03.guard import assert_no_authority_modules, authority_modules_loaded
from w03.launcher import (
    assert_effective_config,
    assert_loopback_command,
    build_command,
)
from w03.project import load_project

PROJECT = load_project()


def test_three_tasks_have_distinct_statuses() -> None:
    t1 = compose(PROJECT, "T1")
    t2 = compose(PROJECT, "T2")
    t3 = compose(PROJECT, "T3")
    assert t1.status == "CONDITIONAL"
    assert t2.status == "HOLD"
    assert t3.status == "REJECTED_PREMISE"
    assert "privacy" in t1.answer.lower()
    assert "UNKNOWN" in t2.answer
    assert "premise" in t3.answer.lower()


def test_wrong_scope_and_untrusted_are_not_answer_evidence() -> None:
    result = compose(PROJECT, "T1")
    cited = {item.id for item in result.evidence + result.dissent + result.alternatives + result.unresolved + result.revision}
    assert "s5" not in cited
    assert "s7" not in cited
    assert "s5" in {item.id for item in result.excluded}
    assert "s7" in {item.id for item in result.untrusted}


def test_answer_hash_is_mode_independent_and_expansion_does_not_mutate() -> None:
    hashes = {compose(PROJECT, "T1", mode).answer_hash for mode in ("BASELINE", "OPTIONAL", "FORCED")}
    assert len(hashes) == 1
    result = compose(PROJECT, "T1")
    before = result.answer_hash
    payload = expansion(result)
    assert set(payload) == set(EXPANSIONS)
    assert result.answer_hash == before
    assert compose(PROJECT, "T1").answer_hash == before


def test_export_is_allowlisted_and_portable() -> None:
    result = compose(PROJECT, "T1")
    payload = finalize(build_payload(PROJECT.project_id, result))
    assert set(payload) <= ALLOWED_KEYS
    assert payload["synthetic"] is True
    assert len(payload["export_sha256"]) == 64
    blob = json.dumps(payload, ensure_ascii=False)
    assert "C:\\" not in blob
    assert "/Users/" not in blob
    assert "runtime_dir" not in blob
    assert "source_locator" not in blob
    md = export_markdown(PROJECT.project_id, result)
    js = export_json(PROJECT.project_id, result)
    assert "export_sha256" in md
    assert json.loads(js)["task_id"] == "T1"


def test_refusal_and_rejection_are_terminal_messages() -> None:
    assert refusal_response("T1")["status"] == "STOPPED"
    assert "nothing is recorded" in refusal_response("T1")["message"].lower()
    assert reject_question("T2")["status"] == "QUESTION_REJECTED"


def test_no_authority_modules_are_loaded() -> None:
    assert authority_modules_loaded() == []
    assert_no_authority_modules()
    source = (PROJECT and __import__("pathlib").Path(__file__).resolve().parents[1] / "gui-or-slice" / "streamlit_app.py").read_text(encoding="utf-8")
    assert "team_soul." not in source


def test_launcher_is_loopback_and_fail_closed_without_flag() -> None:
    command = build_command(port=8599)
    assert_loopback_command(command)
    assert "--server.address" in command
    assert command[command.index("--server.address") + 1] == "127.0.0.1"
    assert "--browser.gatherUsageStats" in command
    assert command[command.index("--browser.gatherUsageStats") + 1] == "false"
    assert "--client.showErrorDetails" in command
    assert command[command.index("--client.showErrorDetails") + 1] == "type"
    without = build_command(port=8599, force_loopback=False)
    with pytest.raises(AssertionError):
        assert_loopback_command(without)
    assert assert_effective_config()["address"] == "ok"
