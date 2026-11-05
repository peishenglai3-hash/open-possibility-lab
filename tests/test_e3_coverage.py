"""E3 coverage invariants for the six perturbation families added in epoch 2.

Each test builds the representative perturbed case from the frozen scenario
matrix, runs it through the real evaluator (:func:`team_soul.eval.run_case`)
with a default :class:`RuntimeConfig`, and asserts that the perturbation does
not change the correct outcome of its source case. One representative case per
family is structural coverage, not statistical power.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from team_soul.config import RuntimeConfig
from team_soul.contracts import EvalCase, RawEvent
from team_soul.eval import run_case
from team_soul.ingest import load_fixtures
from team_soul.scenario import ScenarioCase, build_scenario_matrix

# The six families the epoch-2 handoff requires. Kept as an independent literal
# so a silently dropped family fails the suite instead of shrinking it.
REQUIRED_E3_FAMILIES = (
    "future_leak",
    "false_summary",
    "noise_reopen",
    "majority_over_dissent",
    "invalid_transfer_high_similarity",
    "fake_consensus",
)


@lru_cache(maxsize=None)
def _matrix() -> tuple[ScenarioCase, ...]:
    return tuple(build_scenario_matrix("fixtures"))


@lru_cache(maxsize=None)
def _base_case(case_id: str) -> EvalCase:
    return next(case for case in load_fixtures("fixtures") if case.case_id == case_id)


@lru_cache(maxsize=None)
def _perturbed_case(family: str) -> ScenarioCase:
    matches = [item for item in _matrix() if item.spec.perturbation == family]
    assert len(matches) == 1, f"expected exactly one E3 case for {family!r}"
    case = matches[0]
    assert case.spec.evidence_level == "E3"
    assert case.spec.environment == "Perturbation"
    assert case.spec.tags[:2] == ("adversarial", family)
    assert case.spec.episode_id == f"fixture:{case.spec.source_case_id}"
    # A perturbation must not change the correct outcome, so the expectations
    # remain the base expectations: no transform removes evidence, and none may
    # weaken or re-denominate a metric.
    assert case.case.expected == _base_case(case.spec.source_case_id).expected
    return case


def _added_events(family: str) -> list[RawEvent]:
    """Return the events the transform injected, or fail the test if it is a no-op."""

    case = _perturbed_case(family)
    base_ids = {event.id for event in _base_case(case.spec.source_case_id).events}
    added = [event for event in case.case.events if event.id not in base_ids]
    assert added, f"{family!r} injected no event: the perturbation is not exercised"
    return added


def _run(family: str) -> dict[str, Any]:
    return run_case(_perturbed_case(family).case, RuntimeConfig())


def test_future_leak_after_the_decision_time_keeps_closure_active() -> None:
    case = _perturbed_case("future_leak")
    base = _base_case("C01")
    assert case.case.decision_time == max(event.timestamp for event in base.events)
    leaked = _added_events("future_leak")
    assert len(leaked) == 1
    assert leaked[0].timestamp > case.case.decision_time
    result = _run("future_leak")
    assert result["closure"] is not None
    assert result["closure"]["status"] == "ACTIVE"


def test_false_summary_cannot_displace_raw_history() -> None:
    added = _added_events("false_summary")
    assert len(added) == 1
    result = _run("false_summary")
    assert result["closure"] is not None
    assert result["closure"]["status"] == "ACTIVE"
    historical_ids = set(_base_case("C01").expected["required_event_ids"])
    soul = result["arms"]["SOUL"]
    # The summary must not push the raw history out of the retrievable pool.
    assert historical_ids <= set(soul["retrieval"]["candidate_pool_ids"])
    assert historical_ids <= set(soul["context"]["selected_event_ids"])


def test_noise_reopen_does_not_reopen_an_active_closure() -> None:
    added = _added_events("noise_reopen")
    assert len(added) == 1
    result = _run("noise_reopen")
    assert result["closure"] is not None
    assert result["closure"]["status"] == "ACTIVE"
    assert result["closure"]["status"] != "REOPENED"
    assert result["reopen_signals"] == []


def test_majority_over_dissent_preserves_the_dissent() -> None:
    added = _added_events("majority_over_dissent")
    assert len(added) == 3
    dissent_ids = set(_base_case("C03").expected["dissent_event_ids"])
    assert dissent_ids
    result = _run("majority_over_dissent")
    for arm in ("RAG", "SOUL"):
        metrics = result["arms"][arm]["metrics"]
        assert metrics["dissent_preservation_rate"] == 1.0
        assert dissent_ids <= set(result["arms"][arm]["context"]["selected_event_ids"])


def test_invalid_transfer_high_similarity_still_rejects() -> None:
    added = _added_events("invalid_transfer_high_similarity")
    assert len(added) == 3
    transfer = _perturbed_case("invalid_transfer_high_similarity").case.transfer
    assert transfer is not None
    assert transfer.forbidden_conditions
    result = _run("invalid_transfer_high_similarity")
    assert result["transfer"] is not None
    assert result["transfer"]["decision"] == "REJECT"
    for arm in ("BASE", "RAG", "SOUL"):
        assert result["arms"][arm]["metrics"]["negative_transfer_rejection"] == 1.0


def test_fake_consensus_cannot_create_closure() -> None:
    added = _added_events("fake_consensus")
    assert len(added) == 4
    result = _run("fake_consensus")
    assert result["closure"] is not None
    assert result["closure"]["status"] == "NO_CLOSURE"
    assert result["closure"]["status"] != "ACTIVE"
    for arm in ("BASE", "RAG", "SOUL"):
        metrics = result["arms"][arm]["metrics"]
        assert metrics["false_closure_rate"] == 0.0
        assert metrics["team_level_overreach"] == 0.0
