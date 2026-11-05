"""Every approved historical-replay case must survive a real projection.

`load_historical_replay` only validates the E2 envelope and the `EvalCase` schema. Event
`metadata` is a free-form dict, so a marker that is legal there can still be illegal once
`projection.rebuild_projection` turns curated claims into `Claim` contracts, which forbid
extra fields. That gap was found the hard way: the C71 E1 marker was added at claim level
and HIST-C71 then raised `extra_forbidden` during projection.

These tests replay every approved E2 case end to end with the deterministic mock provider,
so a metadata marker can never again be approved without being projected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from team_soul.config import RuntimeConfig
from team_soul.eval import run_case
from team_soul.scenario import load_historical_replay

REPO = Path(__file__).resolve().parents[1]
CANDIDATES = REPO / "e2_candidates"


def _approved_cases():
    if not CANDIDATES.is_dir():
        pytest.skip(
            "authorised E2 candidates are not distributed in the public snapshot",
            allow_module_level=True,
        )
    cases, status = load_historical_replay(CANDIDATES)
    if status["status"] != "PASS":
        pytest.skip(
            "approved E2 cases are not available in this environment",
            allow_module_level=True,
        )
    assert cases, "no approved E2 case was available to replay"
    return cases


@pytest.mark.parametrize("case", _approved_cases(), ids=lambda item: item.spec.scenario_id)
def test_approved_historical_case_projects_and_replays(case) -> None:
    config = RuntimeConfig(provider_kind="mock", provider_model="mock-v0.1")
    result = run_case(case.case, config, arms=("SOUL",))
    assert result["projection_stable"] is True
    assert result["raw_history_hash"]
    assert result["arms"]["SOUL"]["run_id"]
