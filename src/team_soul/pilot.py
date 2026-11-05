from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from .config import RuntimeConfig
from .contracts import Contract, EvalCase, RawEvent
from .hashing import sha256_json
from .operators import check_transfer, evaluate_closure
from .projection import rebuild_projection
from .retrieval import retrieve
from .steering_audit import audit_steering
from .storage import Store


PilotRating = Literal["helpful", "neutral", "wrong", "harmful", "needs_context"]
EvidenceLevelCode = Literal["E0", "E1", "E2", "E3", "E4"]


class LearningContextRef(Contract):
    """Privacy-bounded context reference; raw snippets are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_hash: str
    evidence_refs: tuple[str, ...] = ()
    scope: str = "LOCAL"


class LearnableSupervisionSignal(Contract):
    """Future training tuple; recorded only as evidence, never trained in V0.2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    context: LearningContextRef
    candidate_action: dict[str, Any]
    human_correction: dict[str, Any] | None = None
    accepted_state: dict[str, Any] | None = None
    downstream_outcome: dict[str, Any] | str | None = None
    evidence_level: EvidenceLevelCode = "E0"


class PilotSuggestion(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suggestion_id: str
    kind: Literal["retrieve", "transfer_candidate", "closure_proposal"]
    status: Literal["PROPOSED"] = "PROPOSED"
    evidence_refs: list[str] = Field(default_factory=list)
    actor_ids: list[str] = Field(default_factory=list)
    is_dissent: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    scope: str = "LOCAL"
    applied_to_project: Literal[False] = False
    requires_human_action: Literal[True] = True


class PilotFeedback(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    suggestion_id: str
    case_id: str
    actor_id: str = Field(min_length=1, max_length=100)
    rating: PilotRating
    downstream_outcome: str | None = Field(default=None, max_length=500)
    learnable_signal: LearnableSupervisionSignal | None = None
    live_use_confirmed: bool = False
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_signal_evidence(self) -> "PilotFeedback":
        if (
            self.learnable_signal is not None
            and self.learnable_signal.evidence_level == "E4"
            and not self.live_use_confirmed
        ):
            raise ValueError("E4 supervision requires explicit live-use confirmation")
        return self


def preview_pilot(case: EvalCase, config: RuntimeConfig) -> dict[str, Any]:
    """Create local suggestion proposals and a SteeringAudit without project writes or provider calls."""

    store = Store(":memory:")
    store.init_schema()
    store.append_events(case.events)
    raw_before = _raw_history_hash(store)
    projection_hash = rebuild_projection(store)
    retrieval_result = retrieve(store, case.query, "SOUL", config)
    selected_ids = [item.event_id for item in retrieval_result.selected]
    selected_events = {event.id: event for event in case.events}
    suggestions: list[PilotSuggestion] = [
        _suggestion(
            case.case_id,
            "retrieve",
            [item.event_id],
            [selected_events[item.event_id].actor_id] if item.event_id in selected_events else [],
            reasons=[item.selection_reason, str(item.evidence_kind)],
            is_dissent=(
                str(item.evidence_kind).lower() in {"dissent", "veto"}
                or (item.event_id in selected_events and _is_dissent(selected_events[item.event_id]))
            ),
        )
        for item in retrieval_result.selected
    ]
    if not suggestions:
        suggestions.append(_suggestion(case.case_id, "retrieve", [], [], reasons=["NO_VERIFIED_EVIDENCE"]))
    transfer = check_transfer(case.transfer) if case.transfer else None
    if transfer is not None:
        suggestions.append(
            _suggestion(
                case.case_id,
                "transfer_candidate",
                [ref.source_event_id for ref in transfer.evidence_refs],
                sorted({event.actor_id for event in case.events if event.id in {ref.source_event_id for ref in transfer.evidence_refs}}),
                reasons=[str(transfer.decision), *transfer.reason_codes],
            )
        )
    closure = evaluate_closure(case.closure_proposal, store, config) if case.closure_proposal else None
    if closure is not None:
        closure_refs = [ref.source_event_id for ref in closure.supporting_evidence]
        suggestions.append(
            _suggestion(
                case.case_id,
                "closure_proposal",
                closure_refs,
                sorted({event.actor_id for event in case.events if event.id in set(closure_refs)}),
                reasons=[str(closure.status), *closure.reason_codes],
                scope=str(closure.level),
            )
        )
    before_options = [
        {
            "option_id": f"event:{event.id}",
            "evidence_refs": [event.id],
            "actor_ids": [event.actor_id],
            "is_dissent": _is_dissent(event),
            "direction": "SOURCE_EVIDENCE",
        }
        for event in case.events
    ]
    after_options = [suggestion.model_dump(mode="json") for suggestion in suggestions]
    audit = audit_steering(
        before_options,
        after_options,
        suggestion_direction=f"{len(suggestions)} scoped suggestions generated",
        closure_direction=str(closure.status) if closure is not None else "NO_CLOSURE_PROPOSAL",
    )
    raw_after = _raw_history_hash(store)
    store.close()
    return {
        "schema_version": "pilot-preview.v0.2.d8",
        "mode": "DRY_RUN_SUGGESTION_ONLY",
        "case_id": case.case_id,
        "evidence_level": "E1" if case.case_id.startswith("C") else "NOT CLASSIFIED",
        "projection_hash": projection_hash,
        "retrieval": retrieval_result.model_dump(mode="json"),
        "transfer": transfer.model_dump(mode="json") if transfer else None,
        "closure": closure.model_dump(mode="json") if closure else None,
        "suggestions": [item.model_dump(mode="json") for item in suggestions],
        "steering_audit": audit.model_dump(mode="json"),
        "raw_history_hash_before": raw_before,
        "raw_history_hash_after": raw_after,
        "raw_history_unchanged": raw_before == raw_after,
        "provider_called": False,
        "project_mutated": False,
        "human_feedback": "NOT OBSERVED / OPEN GAP",
        "live_pilot_evidence": "NOT OBSERVED / OPEN GAP",
    }


def run_pilot_d8_suite(
    fixture_dir: str | Path,
    config: RuntimeConfig,
    *,
    output_json: str | Path = "docs/PILOT_NOTES_D8.json",
    output_markdown: str | Path = "docs/PILOT_NOTES.md",
    output_steering_json: str | Path = "docs/STEERING_AUDIT_D8.json",
) -> dict[str, Any]:
    """Run suggestion-only previews over supplied fixtures; never call this E4."""

    from .ingest import load_fixtures

    cases = load_fixtures(fixture_dir)
    previews = [preview_pilot(case, config) for case in cases]
    report = {
        "schema_version": "pilot-notes.v0.2.d8",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "FIXTURE_DRY_RUN_ONLY",
        "case_count": len(previews),
        "evidence_counts": {"E1": len(previews), "E4": 0},
        "all_suggestions_proposals": all(
            all(
                item["status"] == "PROPOSED"
                and item["applied_to_project"] is False
                and item["requires_human_action"] is True
                for item in preview["suggestions"]
            )
            for preview in previews
        ),
        "raw_history_unchanged": all(preview["raw_history_unchanged"] for preview in previews),
        "provider_calls": sum(bool(preview["provider_called"]) for preview in previews),
        "steering_audit_records": [preview["steering_audit"] for preview in previews],
        "cases": [
            {
                "case_id": preview["case_id"],
                "evidence_level": preview["evidence_level"],
                "suggestion_count": len(preview["suggestions"]),
                "steering_disposition": preview["steering_audit"]["disposition"],
                "steering_reason_codes": preview["steering_audit"]["reason_codes"],
                "raw_history_unchanged": preview["raw_history_unchanged"],
            }
            for preview in previews
        ],
        "human_feedback": {
            "count": 0,
            "status": "NOT OBSERVED / OPEN GAP",
            "source": "No live-pilot review records were supplied for this run.",
        },
        "real_failure_repair": {
            "count": 0,
            "status": "NOT OBSERVED / OPEN GAP",
            "reason": "No E2/E4 human-confirmed failure was available to repair.",
        },
        "e4_live_pilot": "NOT OBSERVED / OPEN GAP",
    }
    target = Path(output_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path = Path(output_steering_json)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report["steering_audit_records"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = Path(output_markdown)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_pilot_markdown(report), encoding="utf-8")
    return report


def render_pilot_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Team Soul V0.2 D8 Pilot Notes",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Mode: `{report['mode']}`; cases: `{report['case_count']}`; provider calls: `{report['provider_calls']}`",
        "",
        "| Case | Level | Suggestions | Steering | Reasons | Raw history |",
        "|---|---|---:|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['evidence_level']} | {case['suggestion_count']} | "
            f"{case['steering_disposition']} | {', '.join(case['steering_reason_codes']) or 'none'} | "
            f"{'PASS' if case['raw_history_unchanged'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Human evidence",
            "",
            f"- Live review records: `{report['human_feedback']['status']}` (count `{report['human_feedback']['count']}`)",
            f"- E4 pilot: `{report['e4_live_pilot']}`",
            f"- Real failure repair: `{report['real_failure_repair']['status']}`",
            "- Fixture dry-runs are E1 and are not promoted to pilot evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def append_pilot_feedback(store: Store, feedback: PilotFeedback) -> str:
    """Append a structured human review to the evaluation evidence ledger."""

    payload = feedback.model_dump(mode="json")
    return store.append_pilot_feedback(payload)


def load_pilot_case(fixture_path: str | Path) -> EvalCase:
    from .ingest import load_fixture

    return load_fixture(fixture_path)


def _suggestion(
    case_id: str,
    kind: Literal["retrieve", "transfer_candidate", "closure_proposal"],
    evidence_refs: list[str],
    actor_ids: list[str],
    *,
    reasons: list[str],
    scope: str = "LOCAL",
    is_dissent: bool = False,
) -> PilotSuggestion:
    payload = {"case_id": case_id, "kind": kind, "evidence_refs": evidence_refs, "reasons": reasons}
    return PilotSuggestion(
        suggestion_id=f"suggestion:{sha256_json(payload)[:20]}",
        kind=kind,
        evidence_refs=evidence_refs,
        actor_ids=actor_ids,
        is_dissent=is_dissent,
        reason_codes=reasons,
        scope=scope,
    )


def _raw_history_hash(store: Store) -> str:
    return sha256_json([(event.id, event.content_hash) for event in store.list_events()])


def _is_dissent(event: RawEvent) -> bool:
    roles = event.metadata.get("roles", event.metadata.get("tags", []))
    if isinstance(roles, str):
        roles = [roles]
    return any(str(role).lower() in {"dissent", "veto", "contradiction"} for role in roles)


__all__ = [
    "LearnableSupervisionSignal",
    "LearningContextRef",
    "PilotFeedback",
    "PilotSuggestion",
    "append_pilot_feedback",
    "load_pilot_case",
    "preview_pilot",
    "render_pilot_markdown",
    "run_pilot_d8_suite",
]
