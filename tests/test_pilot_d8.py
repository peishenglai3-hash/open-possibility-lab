from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from team_soul.config import RuntimeConfig
from team_soul.pilot import (
    LearnableSupervisionSignal,
    LearningContextRef,
    PilotFeedback,
    append_pilot_feedback,
    load_pilot_case,
    preview_pilot,
    run_pilot_d8_suite,
)
from team_soul.steering_audit import audit_steering
from team_soul.storage import Store


def test_steering_audit_escalates_actor_and_dissent_loss_despite_preference() -> None:
    before = [
        {"evidence_refs": ["e1"], "actor_ids": ["actor-a"], "is_dissent": False},
        {"evidence_refs": ["e2"], "actor_ids": ["actor-b"], "is_dissent": True},
    ]
    after = [{"evidence_refs": ["e1"], "actor_ids": ["actor-a"], "is_dissent": False}]
    audit = audit_steering(
        before,
        after,
        suggestion_direction="close quickly",
        closure_direction="ACTIVE",
        user_preference_score=1.0,
    )

    assert audit.excluded_actor_ids == ["actor-b"]
    assert audit.dissent_removed is True
    assert audit.disposition == "ESCALATE_HUMAN_REVIEW"
    assert audit.user_preference_score == 1.0
    assert "ACTOR_EVIDENCE_EXCLUDED" in audit.reason_codes
    assert "PROTECTED_DISSENT_REMOVED" in audit.reason_codes


def test_pilot_preview_is_suggestion_only_and_preserves_raw_history() -> None:
    case = load_pilot_case("fixtures/C02_theory_overclaim.json")
    report = preview_pilot(case, RuntimeConfig())

    assert report["mode"] == "DRY_RUN_SUGGESTION_ONLY"
    assert report["raw_history_unchanged"] is True
    assert report["provider_called"] is False
    assert report["project_mutated"] is False
    assert all(item["status"] == "PROPOSED" for item in report["suggestions"])
    assert all(item["applied_to_project"] is False for item in report["suggestions"])
    assert report["live_pilot_evidence"] == "NOT OBSERVED / OPEN GAP"


def test_pilot_feedback_is_append_only_and_separate_from_raw_events() -> None:
    store = Store(":memory:")
    store.init_schema()
    feedback = PilotFeedback(
        review_id="review-1",
        suggestion_id="suggestion-1",
        case_id="case-1",
        actor_id="participant-opaque-1",
        rating="needs_context",
        downstream_outcome="requested provenance for the old result",
        learnable_signal=LearnableSupervisionSignal(
            signal_id="signal:review-1",
            context=LearningContextRef(
                context_hash="context-hash-1",
                evidence_refs=("event:synthetic-1",),
                scope="LOCAL",
            ),
            candidate_action={"kind": "retrieve", "suggestion_id": "suggestion-1"},
            human_correction={"reason_code": "MISSING_DISSENT"},
            accepted_state={"accepted": False},
            downstream_outcome={"review_status": "needs_context"},
            evidence_level="E1",
        ),
        live_use_confirmed=True,
        reviewed_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
    )

    first_id = append_pilot_feedback(store, feedback)
    second_id = append_pilot_feedback(store, feedback)

    assert first_id == second_id
    assert store.list_pilot_feedback() == [feedback.model_dump(mode="json")]
    assert store.list_events() == []
    try:
        store.connection.execute("DELETE FROM pilot_feedback")
        raise AssertionError("append-only trigger did not reject DELETE")
    except sqlite3.IntegrityError:
        pass
    store.close()


def test_e4_supervision_cannot_be_self_labeled_without_live_use_confirmation() -> None:
    with pytest.raises(ValidationError, match="E4 supervision"):
        PilotFeedback(
            review_id="review-e4-unverified",
            suggestion_id="suggestion-e4",
            case_id="case-e4",
            actor_id="participant-opaque",
            rating="wrong",
            learnable_signal=LearnableSupervisionSignal(
                signal_id="signal:e4-unverified",
                context=LearningContextRef(context_hash="ctx-hash"),
                candidate_action={"kind": "retrieve"},
                evidence_level="E4",
            ),
            live_use_confirmed=False,
        )


def test_d8_fixture_suite_does_not_claim_live_pilot_or_real_failure(tmp_path) -> None:
    report = run_pilot_d8_suite(
        "fixtures",
        RuntimeConfig(),
        output_json=tmp_path / "PILOT_NOTES_D8.json",
        output_markdown=tmp_path / "PILOT_NOTES.md",
        output_steering_json=tmp_path / "STEERING_AUDIT_D8.json",
    )

    assert report["case_count"] == 5
    assert report["evidence_counts"] == {"E1": 5, "E4": 0}
    assert report["all_suggestions_proposals"] is True
    assert report["raw_history_unchanged"] is True
    assert report["provider_calls"] == 0
    assert report["human_feedback"]["status"] == "NOT OBSERVED / OPEN GAP"
    assert report["real_failure_repair"]["status"] == "NOT OBSERVED / OPEN GAP"
    assert (tmp_path / "STEERING_AUDIT_D8.json").exists()
