"""G0-B: evaluator isolation must be L1 process-memory isolation on the real path.

FR-11: the candidate runner could reach the held-out oracle from inside the same process
while the report still said oracle_isolation: True.
"""

from __future__ import annotations

import pytest

from team_soul.kernel.isolation import (
    ISOLATION_LEVEL,
    ORACLE_CANARY_KEY,
    REPORT_LABEL,
    IsolationError,
    oracle_reach_probe,
    public_payload,
    run_isolated,
)

ORACLE_STATE = {ORACLE_CANARY_KEY: {"provenance_coverage": 1.0}}


def _in_process_attack() -> dict:
    """Evaluator-side function holding oracle state in a local, then invoking the candidate."""

    oracle = ORACLE_STATE
    assert oracle[ORACLE_CANARY_KEY]
    return oracle_reach_probe(PUBLIC)

PUBLIC = {
    "case_id": "SYN-C01",
    "query": "what changed",
    "events": [{"id": "evt_c01_1"}],
    "budget_tokens": 6000,
}


def test_tb1_in_process_candidate_reaches_the_oracle_control() -> None:
    """Control: without isolation the frame-chain attack succeeds (the FR-11 path)."""

    assert _in_process_attack()["oracle_found"] is True


def test_tb2_isolated_candidate_cannot_reach_the_oracle() -> None:
    """G0-B: the same candidate, spawned, sees a fresh interpreter with no oracle."""

    assert ORACLE_STATE[ORACLE_CANARY_KEY]
    result = run_isolated("team_soul.kernel.isolation:oracle_reach_probe", PUBLIC)
    assert result["oracle_found"] is False
    assert result["keys"] == sorted(PUBLIC)


def test_tb3_public_contract_is_frozen_and_evaluator_keys_are_refused() -> None:
    for key in ("expected", "oracle_metrics", "closure_status", "split"):
        with pytest.raises(IsolationError):
            public_payload({**PUBLIC, key: "evaluator-only"})
    frozen = public_payload(PUBLIC)
    frozen["events"].append({"id": "mutated"})
    assert PUBLIC["events"] == [{"id": "evt_c01_1"}]


def test_tb5_child_environment_is_scrubbed(monkeypatch) -> None:
    """A spawn child inherits the parent env; secret-shaped names must be removed."""

    monkeypatch.setenv("TEAM_SOUL_API_KEY", "sk-test-should-not-leak")
    result = run_isolated("team_soul.kernel.isolation:env_probe", PUBLIC)
    assert result["api_key_present"] is False


def test_tb6_child_runs_in_a_private_working_directory() -> None:
    """Relative-path and manifest inference must not reach the evaluator workspace."""

    result = run_isolated("team_soul.kernel.isolation:cwd_probe", PUBLIC)
    assert "team-soul-isolated-" in result["cwd"]
    assert result["manifests"] == []


def test_tb4_reports_must_use_the_l1_label() -> None:
    assert ISOLATION_LEVEL == "L1"
    assert REPORT_LABEL == "process_isolation_only"
