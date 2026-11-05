"""G0-C / G0-D / G0-E / G0-F tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from team_soul.contracts import ActorType, EvidenceKind, EvidenceRef, RawEvent
from team_soul.exposure import ActorView, TeamView
from team_soul.kernel import SandboxDecision, SandboxReplayResult
from team_soul.kernel.receipts import evaluator_code_hash, issue_sandbox_receipt, verify_sandbox_receipt
from team_soul.projection import load_problems, rebuild_projection
from team_soul.storage import CorruptRawEvent, Store

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _result(candidate: str = "policy:cand", version: str = "1.0.0") -> SandboxReplayResult:
    return SandboxReplayResult(
        decision=SandboxDecision.HUMAN_GATE_REQUIRED,
        old_policy_id="policy:base",
        candidate_policy_id=candidate,
        old_projection_hash="0" * 64,
        new_projection_hash="f" * 64,
        disabled_projection_hash="0" * 64,
        rebuilt_projection_hash="f" * 64,
        raw_history_hash_before="a" * 64,
        raw_history_hash_after="a" * 64,
        invariant_regressions=(),
        reason_codes=("REPLAY_PASS",),
    )


def _issue(**extra):
    result = _result()
    return result, issue_sandbox_receipt(
        result,
        candidate_policy_id=result.candidate_policy_id,
        candidate_policy_version="1.0.0",
        code_hash="code",
        suite_hash="suite",
        corpus_hash="corpus",
        split_hash="split",
        config_hash="config",
        evaluator_hash=evaluator_code_hash(),
        run_id="sandbox:run",
        issued_at=NOW,
        **extra,
    )


def test_tc1_bindings_are_verified_when_present() -> None:
    result, receipt = _issue(transition_id="transition:1", base_policy_version="0.9.0")
    assert receipt.transition_id == "transition:1"
    assert verify_sandbox_receipt(
        result, receipt, candidate_policy_id="policy:cand", candidate_policy_version="1.0.0",
        transition_id="transition:1", base_policy_version="0.9.0",
    )
    assert not verify_sandbox_receipt(
        result, receipt, candidate_policy_id="policy:cand", candidate_policy_version="1.0.0",
        transition_id="transition:2", base_policy_version="0.9.0",
    )
    assert not verify_sandbox_receipt(
        result, receipt, candidate_policy_id="policy:cand", candidate_policy_version="1.0.0",
        transition_id="transition:1", base_policy_version="9.9.9",
    )


def test_tc2_cross_candidate_and_stale_result_are_refused() -> None:
    result, receipt = _issue()
    assert not verify_sandbox_receipt(
        result, receipt, candidate_policy_id="policy:other", candidate_policy_version="1.0.0"
    )
    stale = _result(version="2.0.0")
    assert not verify_sandbox_receipt(
        stale, receipt, candidate_policy_id="policy:cand", candidate_policy_version="2.0.0"
    )


def test_tc3_receipts_without_bindings_still_verify() -> None:
    result, receipt = _issue()
    assert verify_sandbox_receipt(
        result, receipt, candidate_policy_id="policy:cand", candidate_policy_version="1.0.0"
    )


def _event(event_id: str, actor: str, roles: list[str] | None = None, problem: str = "problem:p") -> RawEvent:
    return RawEvent(
        id=event_id,
        timestamp=NOW,
        actor_id=actor,
        actor_type=ActorType.HUMAN,
        source_type="t",
        source_locator=f"t/{event_id}",
        raw_content=f"body {event_id}",
        problem_hints=[problem],
        metadata={"roles": roles} if roles else {},
    )


def test_td1_speculative_event_cannot_mint_problem_state(tmp_path) -> None:
    store = Store(tmp_path / "g0d.sqlite3")
    store.init_schema()
    speculative = RawEvent(
        id="evt:speculative",
        timestamp=NOW,
        actor_id="team-soul-runtime",
        actor_type=ActorType.MODEL,
        source_type="generative",
        source_locator="generative/candidate",
        raw_content="invented direction",
        problem_hints=["problem:invented"],
        metadata={"event_kind": "generative_candidate", "speculative": True},
    )
    store.append_events([speculative, _event("evt:real", "alice")])
    rebuild_projection(store)
    ids = {problem.id for problem in load_problems(store)}
    assert "problem:invented" not in ids
    assert "problem:p" in ids
    store.close()


def test_te1_actor_view_is_a_subset_of_team_view_and_coverage_is_explicit(tmp_path) -> None:
    store = Store(tmp_path / "g0e.sqlite3")
    store.init_schema()
    store.append_events([
        _event("evt:a", "alice"),
        _event("evt:b", "bob", roles=["dissent"]),
    ])
    rebuild_projection(store)
    actor = ActorView(store, "alice", NOW + timedelta(days=1))
    team = TeamView(store, NOW + timedelta(days=1))
    assert actor.event_ids() <= {event.id for event in team.events()}
    coverage = team.coverage()
    assert coverage["actor_ids"] == ["alice", "bob"]
    assert "evt:b" in coverage["unseen_to"]["alice"]
    assert coverage["contested_by"] == ["bob"]
    store.close()


def test_tf1_corrupt_row_is_quarantined_not_bricking(tmp_path) -> None:
    store = Store(tmp_path / "g0f.sqlite3")
    store.init_schema()
    store.append_event(_event("evt:good", "alice"))
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO raw_events (id, timestamp, actor_id, actor_type, source_type, "
            "source_locator, raw_content, project_hints_json, problem_hints_json, "
            "metadata_json, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "evt:tampered", "2026-09-24T00:00:00+00:00", "bob", "human", "t", "t/2",
                "tampered body", "[]", "[]", "{}", "deadbeef",
            ),
        )
    ids = [event.id for event in store.list_events()]
    assert "evt:good" in ids and "evt:tampered" not in ids
    assert any(item["id"] == "evt:tampered" for item in store.corrupt_raw_events)
    with pytest.raises(CorruptRawEvent):
        store.get_event("evt:tampered")
    store.close()
