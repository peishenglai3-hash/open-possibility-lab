from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from .contracts import Contract
from .hashing import sha256_json


class SteeringAuditRecord(Contract):
    audit_id: str
    before_options: list[dict[str, Any]]
    after_options: list[dict[str, Any]]
    actor_evidence_distribution: dict[str, dict[str, int]]
    suggestion_direction: str
    closure_direction: str
    excluded_actor_ids: list[str] = Field(default_factory=list)
    dissent_removed: bool = False
    user_preference_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    disposition: str = "OBSERVE"


def audit_steering(
    before_options: list[dict[str, Any]],
    after_options: list[dict[str, Any]],
    *,
    suggestion_direction: str,
    closure_direction: str,
    user_preference_score: float | None = None,
) -> SteeringAuditRecord:
    """Detect actor exclusion and dissent loss; preference never cancels either."""

    before_actors = _actor_distribution(before_options)
    after_actors = _actor_distribution(after_options)
    excluded = sorted(actor for actor, count in before_actors.items() if count and not after_actors.get(actor))
    before_dissent = _dissent_refs(before_options)
    after_dissent = _dissent_refs(after_options)
    dissent_removed = bool(before_dissent - after_dissent)
    reason_codes: list[str] = []
    if excluded:
        reason_codes.append("ACTOR_EVIDENCE_EXCLUDED")
    if dissent_removed:
        reason_codes.append("PROTECTED_DISSENT_REMOVED")
    if _closure_strength(closure_direction) > 0 and (
        excluded or dissent_removed
    ):
        reason_codes.append("CLOSURE_STRENGTHENED_WITH_DISTRIBUTION_LOSS")
    disposition = "ESCALATE_HUMAN_REVIEW" if reason_codes else "OBSERVE"
    payload = {
        "before_options": before_options,
        "after_options": after_options,
        "actor_evidence_distribution": {
            "before": dict(sorted(before_actors.items())),
            "after": dict(sorted(after_actors.items())),
        },
        "suggestion_direction": suggestion_direction,
        "closure_direction": closure_direction,
        "excluded_actor_ids": excluded,
        "dissent_removed": dissent_removed,
        "user_preference_score": user_preference_score,
        "reason_codes": sorted(set(reason_codes)),
        "disposition": disposition,
    }
    return SteeringAuditRecord(audit_id=f"steering:{sha256_json(payload)[:20]}", **payload)


def _actor_distribution(options: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for option in options:
        actors = option.get("actor_ids", [])
        if isinstance(actors, str):
            actors = [actors]
        counts.update(str(actor) for actor in actors if actor)
    return counts


def _dissent_refs(options: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for option in options:
        if not option.get("is_dissent", False):
            continue
        refs.update(str(ref) for ref in option.get("evidence_refs", []) if ref)
    return refs


def _closure_strength(value: str) -> int:
    normalized = value.upper()
    if normalized in {"ACTIVE", "CLOSED", "TEAM", "APPROVE", "ACCEPT"}:
        return 2
    if normalized in {"PROPOSED", "CANDIDATE", "PROJECT"}:
        return 1
    return 0


__all__ = ["SteeringAuditRecord", "audit_steering"]
