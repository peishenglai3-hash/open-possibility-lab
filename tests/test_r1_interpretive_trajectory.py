"""MC-R1: the derived InterpretiveTrajectory operator (R1).

Contract: docs/V03-MC-R1.md. These tests exercise the operator on REAL approved E2 history and
pin the three structural rules: provenance is mandatory, ordering prevents backwriting, and a
role proposal may only refine a role.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from team_soul.contracts import RawEvent
from team_soul.scenario import load_historical_replay
from team_soul.trajectory import (
    TRAJECTORY_ROLES,
    derive_interpretive_trajectory,
    validate_trajectory,
)

REPO = Path(__file__).resolve().parents[1]

if not (REPO / "e2_candidates").is_dir():
    pytest.skip(
        "authorised E2 candidates are not distributed in the public snapshot",
        allow_module_level=True,
    )


def _episode(case_id: str):
    cases, status = load_historical_replay(REPO / "e2_candidates")
    assert status["status"] == "PASS"
    match = next(case for case in cases if case.spec.source_case_id == case_id)
    return match


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_derives_the_frozen_roles_from_a_real_episode() -> None:
    case = _episode("C73")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    roles = set(trajectory.roles())
    assert "INITIAL_FRAMING" in roles, trajectory.as_lines()
    assert "COMPETING_INTERPRETATION" in roles, trajectory.as_lines()
    assert "DECISION_OR_CLOSURE" in roles, trajectory.as_lines()
    assert "TURNING_POINT" in roles, trajectory.as_lines()
    assert all(role in TRAJECTORY_ROLES for role in roles)


def test_the_turning_point_is_the_dissent_that_post_dates_the_decision() -> None:
    case = _episode("C73")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    decision = next(node for node in trajectory.nodes if node.role == "DECISION_OR_CLOSURE")
    turning = next(node for node in trajectory.nodes if node.role == "TURNING_POINT")
    assert turning.timestamp > decision.timestamp, (decision, turning)


def test_every_node_binds_a_source_event_and_the_validator_agrees() -> None:
    case = _episode("C73")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    assert all(node.event_ids for node in trajectory.nodes)
    ok, problems = validate_trajectory(trajectory, case.case.events)
    assert ok, problems


def test_nodes_are_ordered_by_source_time_so_backwriting_is_structurally_impossible() -> None:
    case = _episode("C74")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    stamps = [node.timestamp for node in trajectory.nodes]
    assert stamps == sorted(stamps)


def test_the_reading_is_time_bounded_not_latest_state() -> None:
    """Before the dissent was knowable, the trajectory must not contain a turning point."""

    case = _episode("C73")
    before = derive_interpretive_trajectory(
        case.case.case_id, case.case.events, effective_time=_at("2026-09-23T13:00:00Z")
    )
    after = derive_interpretive_trajectory(
        case.case.case_id, case.case.events, effective_time=_at("2026-09-24T05:00:00Z")
    )
    assert "TURNING_POINT" not in before.roles(), before.as_lines()
    assert before.status == "PARTIAL", before.status
    assert "TURNING_POINT" in after.roles(), after.as_lines()


def test_a_role_proposal_cannot_invent_an_event_or_replace_a_role() -> None:
    case = _episode("C73")
    trajectory = derive_interpretive_trajectory(
        case.case.case_id,
        case.case.events,
        role_proposals={
            "evt_does_not_exist": "OUTCOME",
            "evt_c73_6": "SITUATION",
            "evt_c73_1": "NOT_A_ROLE",
        },
    )
    joined = " | ".join(trajectory.notes)
    assert "no such visible event" in joined, joined
    assert "may not be reassigned" in joined, joined
    assert "not in the frozen vocabulary" in joined, joined


def test_validator_rejects_a_fabricated_source_id() -> None:
    case = _episode("C73")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    from dataclasses import replace

    broken = replace(
        trajectory,
        nodes=tuple(
            replace(node, event_ids=("evt_ghost",)) if index == 0 else node
            for index, node in enumerate(trajectory.nodes)
        ),
    )
    ok, problems = validate_trajectory(broken, case.case.events)
    assert not ok
    assert any("does not exist" in problem for problem in problems)


def test_a_record_without_roles_yields_unknown_rather_than_a_guess() -> None:
    event = RawEvent.model_validate(
        {
            "id": "evt_plain",
            "timestamp": "2026-01-01T00:00:00Z",
            "actor_id": "a",
            "actor_type": "human",
            "source_type": "note",
            "source_locator": "n:1",
            "raw_content": "a bare note with no declared role",
        }
    )
    trajectory = derive_interpretive_trajectory("T-PLAIN", [event])
    assert trajectory.status == "PARTIAL", trajectory.status
    assert "INITIAL_FRAMING" not in trajectory.roles()
    assert any("no event carries" in note for note in trajectory.notes) or trajectory.notes


def test_a_rejected_proposal_leaves_the_trajectory_unchanged() -> None:
    """A proposal that is NOT applied must not alter the reading (epoch-4 defect).

    Found by the adversarial reviewer: filtering the deterministic picks through the accepted set
    let an explicitly-unapplied proposal delete DECISION_OR_CLOSURE and TURNING_POINT and flip
    COMPLETE to PARTIAL, while the note said "not applied".
    """

    case = _episode("C73")
    plain = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    proposed = derive_interpretive_trajectory(
        case.case.case_id,
        case.case.events,
        role_proposals={"evt_c73_4": "OUTCOME"},  # an OUTCOME node already exists -> not applied
    )
    assert proposed.roles() == plain.roles(), (plain.as_lines(), proposed.as_lines())
    assert proposed.status == plain.status
    assert any("not applied" in note for note in proposed.notes), proposed.notes


def test_outcome_role_keeps_every_outcome_event() -> None:
    """The old `[:3]` cap silently dropped C76's post-dissent decision (epoch-4 defect)."""

    case = _episode("C76")
    trajectory = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    outcome = next(node for node in trajectory.nodes if node.role == "OUTCOME")
    assert "evt_c76_7" in outcome.event_ids, outcome


def test_an_applied_proposal_fills_a_gap_the_record_left() -> None:
    """The acceptance path had no test at all; it may only fill a gap, never overwrite."""

    case = _episode("C75")
    plain = derive_interpretive_trajectory(case.case.case_id, case.case.events)
    assert "UNRESOLVED" not in plain.roles(), plain.as_lines()
    filled = derive_interpretive_trajectory(
        case.case.case_id, case.case.events, role_proposals={"evt_c75_5": "UNRESOLVED"}
    )
    assert "UNRESOLVED" in filled.roles(), filled.as_lines()
    node = next(node for node in filled.nodes if node.role == "UNRESOLVED")
    assert node.event_ids == ("evt_c75_5",)
    assert "proposed by an external proposer" in node.basis
    ok, problems = validate_trajectory(filled, case.case.events)
    assert ok, problems
