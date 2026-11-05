from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import Field

from .contracts import ActorType, Contract, EvidenceRef, RawEvent, Scope
from .hashing import canonical_json, sha256_json
from .projection import rebuild_projection
from .storage import Store


class GenerativeCandidateType(StrEnum):
    RELATION = "RelationCandidate"
    PROBLEM = "ProblemCandidate"
    HYPOTHESIS = "HypothesisCandidate"
    COUNTERFACTUAL = "CounterfactualCandidate"


class GenerativeCandidateStatus(StrEnum):
    SPECULATIVE = "SPECULATIVE"
    UNVALIDATED = "UNVALIDATED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class GenerativeCandidate(Contract):
    """A speculative, provenance-bound proposal; never a fact or closure."""

    id: str
    candidate_type: GenerativeCandidateType
    proposition: str
    source_event_ids: tuple[str, ...]
    generation_context_hash: str
    generation_context: dict[str, Any] = Field(default_factory=dict)
    scope: Scope = Field(default_factory=Scope)
    status: GenerativeCandidateStatus = GenerativeCandidateStatus.UNVALIDATED
    validation_evidence: tuple[EvidenceRef, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        candidate_type: GenerativeCandidateType,
        proposition: str,
        source_event_ids: tuple[str, ...],
        generation_context_hash: str,
        generation_context: dict[str, Any] | None = None,
        scope: Scope | None = None,
    ) -> "GenerativeCandidate":
        payload = {
            "candidate_type": candidate_type.value,
            "proposition": proposition,
            "source_event_ids": list(source_event_ids),
            "generation_context_hash": generation_context_hash,
            "generation_context": generation_context or {},
        }
        return cls(
            id=f"generative:{sha256_json(payload)[:24]}",
            candidate_type=candidate_type,
            proposition=proposition,
            source_event_ids=source_event_ids,
            generation_context_hash=generation_context_hash,
            generation_context=generation_context or {},
            scope=scope or Scope(),
        )


RelationCandidate = GenerativeCandidate
ProblemCandidate = GenerativeCandidate
HypothesisCandidate = GenerativeCandidate
CounterfactualCandidate = GenerativeCandidate


def persist_candidate(store: Store, candidate: GenerativeCandidate) -> GenerativeCandidate:
    missing = [event_id for event_id in candidate.source_event_ids if store.get_event(event_id) is None]
    if missing:
        raise ValueError("generative candidate has missing source events: " + ", ".join(sorted(missing)))
    payload = {"event_kind": "generative_candidate", "candidate": candidate.model_dump(mode="json")}
    event = RawEvent(
        id=f"evt:{candidate.id}",
        timestamp=candidate.created_at,
        actor_id="team-soul-runtime",
        actor_type=ActorType.TOOL,
        source_type="team_soul.generative",
        source_locator=candidate.id,
        raw_content=canonical_json(payload),
        problem_hints=list(candidate.scope.problem_ids),
        project_hints=list(candidate.scope.project_ids),
        metadata={
            "event_kind": "generative_candidate",
            "generative_candidate": payload,
            "exposed_actor_ids": ["team-soul-runtime"],
        },
    )
    store.append_event(event)
    rebuild_projection(store)
    return candidate


def validate_candidate(
    candidate: GenerativeCandidate,
    evidence: tuple[EvidenceRef, ...],
) -> GenerativeCandidate:
    if not evidence:
        raise ValueError("generative candidate validation requires external evidence")
    return candidate.model_copy(
        update={
            "status": GenerativeCandidateStatus.VALIDATED,
            "validation_evidence": evidence,
        }
    )


def reject_illegal_promotion(candidate: GenerativeCandidate, target: str) -> None:
    """Fail closed: speculative output cannot become a fact/TEAM/closure."""

    raise PermissionError(
        f"generative candidate {candidate.id} cannot be promoted directly to {target}; "
        "human/world validation must create a separate evidence event"
    )


__all__ = [
    "CounterfactualCandidate",
    "GenerativeCandidate",
    "GenerativeCandidateStatus",
    "GenerativeCandidateType",
    "HypothesisCandidate",
    "ProblemCandidate",
    "RelationCandidate",
    "persist_candidate",
    "reject_illegal_promotion",
    "validate_candidate",
]
