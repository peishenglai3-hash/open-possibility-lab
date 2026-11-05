from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import canonical_json, sha256_json

# Epoch 4 (F-2): the single sentinel for "this was not measured". It exists because an empty
# denominator used to render as 1.0 (a perfect score) or 0.0 (a clean score), which let a
# measurement that never happened pass a gate. It is deliberately a string so that it cannot
# be averaged, compared or rendered as a number by accident.
UNSCORED = "UNSCORED"


class ActorType(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    TOOL = "tool"
    DOCUMENT = "document"
    EXTERNAL = "external"


class ProblemStatus(StrEnum):
    HYPOTHESIZED = "HYPOTHESIZED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class ClaimType(StrEnum):
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    DECISION = "DECISION"
    DISSENT = "DISSENT"
    CONSTRAINT = "CONSTRAINT"
    OUTCOME = "OUTCOME"


class ClaimStatus(StrEnum):
    OBSERVED = "OBSERVED"
    HYPOTHESIZED = "HYPOTHESIZED"
    RELATED = "RELATED"
    CONTESTED = "CONTESTED"
    TESTED = "TESTED"
    PROVISIONALLY_ACCEPTED = "PROVISIONALLY_ACCEPTED"
    OPERATIONALLY_CLOSED = "OPERATIONALLY_CLOSED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"
    REOPENED = "REOPENED"
    FORKED = "FORKED"


class EvidenceKind(StrEnum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    DISSENT = "dissent"
    VETO = "veto"
    OUTCOME = "outcome"
    CONTEXT = "context"


class EvidenceStrength(StrEnum):
    HARD = "hard"
    MEDIUM = "medium"
    WEAK = "weak"


class RelationStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class TransferDecision(StrEnum):
    REJECT = "REJECT"
    HOLD = "HOLD"
    UNVERIFIED = "UNVERIFIED"
    CANDIDATE = "CANDIDATE"


class ClosureLevel(StrEnum):
    LOCAL = "LOCAL"
    PROJECT = "PROJECT"
    TEAM = "TEAM"


class ClosureStatus(StrEnum):
    PROPOSED = "PROPOSED"
    NO_CLOSURE = "NO_CLOSURE"
    ACTIVE = "ACTIVE"
    REOPENED = "REOPENED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ReopenTrigger(StrEnum):
    HARD_CONTRADICTION = "HARD_CONTRADICTION"
    CONDITION_CHANGED = "CONDITION_CHANGED"
    OUTCOME_FAILURE = "OUTCOME_FAILURE"
    SCOPE_CHANGED = "SCOPE_CHANGED"
    EXPLICIT_REOPEN_CONDITION = "EXPLICIT_REOPEN_CONDITION"
    HUMAN_CONTEST = "HUMAN_CONTEST"


class DissentStatus(StrEnum):
    PRESERVED = "PRESERVED"
    CONTESTED = "CONTESTED"
    REACTIVATED = "REACTIVATED"
    RESOLVED = "RESOLVED"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class FrozenList(list):
    """A JSON-compatible list that rejects every mutating operation."""

    def __init__(self, values: Any = ()) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("immutable raw event payload")
        list.__init__(self, values)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("immutable raw event payload")

    def __delattr__(self, name: str) -> None:
        raise TypeError("immutable raw event payload")

    def _immutable(self, *_: Any, **__: Any) -> None:
        raise TypeError("immutable raw event payload")

    __delitem__ = __setitem__ = __iadd__ = __imul__ = _immutable
    append = clear = extend = insert = pop = remove = reverse = sort = _immutable


class FrozenDict(dict):
    """A JSON-compatible mapping that rejects every mutating operation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("immutable raw event payload")
        dict.__init__(self, *args, **kwargs)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("immutable raw event payload")

    def __delattr__(self, name: str) -> None:
        raise TypeError("immutable raw event payload")

    def _immutable(self, *_: Any, **__: Any) -> None:
        raise TypeError("immutable raw event payload")

    __delitem__ = __setitem__ = __ior__ = _immutable
    clear = pop = popitem = setdefault = update = _immutable


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return FrozenList(_freeze_value(item) for item in value)
    return value


class Scope(Contract):
    label: str = "UNSCOPED"
    project_ids: list[str] = Field(default_factory=list)
    problem_ids: list[str] = Field(default_factory=list)
    actor_ids: list[str] = Field(default_factory=list)
    scope_basis: list[str] = Field(default_factory=list)
    actor_coverage: list[str] = Field(default_factory=list)
    source_coverage: list[str] = Field(default_factory=list)
    counterevidence_refs: list[str] = Field(default_factory=list)
    validation_basis: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


#: Optional envelope keys introduced by the G0-A dual-time model. While they hold their
#: default (unset) value they are dropped from the canonical envelope, so every pre-G0-A
#: event keeps a byte-identical canonical serialization and content hash (ADR E2).
_OPTIONAL_ENVELOPE_KEYS = ("occurred_at", "known_at", "target_at", "knowledge_basis")
#: Closed vocabulary for the structured provenance that authorises ``known_at`` (ADR E3).
_KNOWLEDGE_BASES = frozenset({"trusted-ingest", "human-approval", "explicit-rule"})


class RawEvent(Contract):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    id: str
    timestamp: datetime
    actor_id: str
    actor_type: ActorType
    source_type: str
    source_locator: str
    raw_content: str
    project_hints: list[str] = Field(default_factory=list)
    problem_hints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # G0-A dual-time model. ``timestamp`` keeps its legacy ledger/audit meaning and is
    # never redefined. ``occurred_at`` = when the recorded thing actually happened;
    # ``known_at`` = earliest time it could legitimately enter the team cognitive horizon;
    # ``target_at`` = a future goal time for plans/predictions (never used for admission).
    occurred_at: datetime | None = None
    known_at: datetime | None = None
    target_at: datetime | None = None
    knowledge_basis: str = ""
    content_hash: str = ""

    @model_validator(mode="after")
    def ensure_time_semantics(self) -> "RawEvent":
        """Validate the optional time fields (ADR C3, C5, C9, E3)."""

        for name in ("occurred_at", "known_at", "target_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"RawEvent.{name} must be timezone-aware (UTC canonical)")
        if self.known_at is not None and self.knowledge_basis not in _KNOWLEDGE_BASES:
            raise ValueError(
                "known_at requires a structured knowledge_basis from a trusted ingestion "
                "path (trusted-ingest | human-approval | explicit-rule); metadata strings, "
                "provider output and LLM proposals may not set it"
            )
        if self.knowledge_basis and self.known_at is None:
            raise ValueError("knowledge_basis is only meaningful together with known_at")
        # ASSUMPTION (recorded in V03-CORRECTION-REGISTER): knowledge cannot precede
        # occurrence. Only checked when both are explicit, because either one defaults to
        # the ledger timestamp, which would otherwise reject legitimate late entries.
        if (
            self.occurred_at is not None
            and self.known_at is not None
            and self.known_at < self.occurred_at
        ):
            raise ValueError("known_at cannot precede occurred_at")
        return self

    @model_validator(mode="after")
    def ensure_hash(self) -> "RawEvent":
        computed = sha256_json(self.hash_payload())
        if not self.content_hash:
            object.__setattr__(self, "content_hash", computed)
        elif self.content_hash != computed:
            raise ValueError("content_hash does not match canonical RawEvent envelope")
        object.__setattr__(self, "project_hints", _freeze_value(self.project_hints))
        object.__setattr__(self, "problem_hints", _freeze_value(self.problem_hints))
        object.__setattr__(self, "metadata", _freeze_value(self.metadata))
        return self

    def verify_content_hash(self) -> bool:
        return self.content_hash == sha256_json(self.hash_payload())

    @property
    def event_time(self) -> datetime:
        """When the recorded thing happened (ADR C2); falls back to the ledger time."""

        return self.occurred_at or self.timestamp

    @property
    def knowledge_time(self) -> datetime:
        """Earliest legitimate knowledge time (ADR C3); falls back to the ledger time."""

        return self.known_at or self.timestamp

    def hash_payload(self) -> dict[str, Any]:
        """Return the canonical envelope covered by ``content_hash``.

        ADR E2: the optional dual-time keys are dropped while unset, so a pre-G0-A event
        keeps a byte-identical canonical serialization and content hash. Setting any of
        them is a deliberate, hash-changing act.
        """

        payload = self.model_dump(mode="json", exclude={"content_hash"})
        for key in _OPTIONAL_ENVELOPE_KEYS:
            if payload.get(key) in (None, ""):
                payload.pop(key, None)
        return payload

    def canonical_serialization(self) -> str:
        """Return a deterministic JSON representation for audit/replay checks.

        Built from ``hash_payload`` so that unset optional time fields do not change the
        audit string of legacy events (ADR E2).
        """

        payload = self.hash_payload()
        payload["content_hash"] = self.content_hash
        return canonical_json(payload)

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "RawEvent":
        """Copy through validation so frozen/deep payload invariants survive updates."""
        payload = self.model_dump(mode="json")
        payload.pop("content_hash", None)
        if update:
            payload.update(deepcopy(update) if deep else update)
        return RawEvent.model_validate(payload)


class EvidenceRef(Contract):
    source_event_id: str
    source_locator: str = ""
    evidence_kind: EvidenceKind
    strength: EvidenceStrength = EvidenceStrength.MEDIUM
    note: str = ""


class Condition(Contract):
    key: str
    source_value: str | None = None
    target_value: str | None = None
    state: Literal["SATISFIED", "MISSING", "UNKNOWN", "CHANGED", "FORBIDDEN"] = "UNKNOWN"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    required: bool = False
    note: str = ""


class ProblemThread(Contract):
    id: str
    title: str
    status: ProblemStatus = ProblemStatus.HYPOTHESIZED
    opened_at: datetime
    closed_at: datetime | None = None
    scope: Scope = Field(default_factory=Scope)
    source_event_ids: list[str] = Field(default_factory=list)
    parent_problem_ids: list[str] = Field(default_factory=list)
    related_problem_ids: list[str] = Field(default_factory=list)
    provenance: list[EvidenceRef] = Field(default_factory=list)


class Claim(Contract):
    id: str
    proposition: str
    type: ClaimType
    source_event_ids: list[str] = Field(default_factory=list)
    problem_ids: list[str] = Field(default_factory=list)
    scope: Scope = Field(default_factory=Scope)
    status: ClaimStatus = ClaimStatus.HYPOTHESIZED
    confidence: float = 0.5


class Relation(Contract):
    src_id: str
    dst_id: str
    relation_type: str
    status: RelationStatus = RelationStatus.CANDIDATE
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    valid_scope: Scope = Field(default_factory=Scope)


class TransferCandidate(Contract):
    source_case_id: str
    target_case_id: str
    shared_conditions: list[Condition] = Field(default_factory=list)
    changed_conditions: list[Condition] = Field(default_factory=list)
    required_conditions: list[Condition] = Field(default_factory=list)
    missing_conditions: list[Condition] = Field(default_factory=list)
    forbidden_conditions: list[Condition] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    external_veto: bool = False
    veto_evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    veto_reason: str = ""
    decision: TransferDecision = TransferDecision.UNVERIFIED
    reason_codes: list[str] = Field(default_factory=list)


class ReopenCondition(Contract):
    key: str
    description: str


class OperationalClosure(Contract):
    id: str
    problem_id: str
    proposition: str
    action: str | None = None
    level: ClosureLevel = ClosureLevel.LOCAL
    scope: Scope = Field(default_factory=Scope)
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    dissent_refs: list[EvidenceRef] = Field(default_factory=list)
    unresolved_refs: list[EvidenceRef] = Field(default_factory=list)
    validation_evidence: list[EvidenceRef] = Field(default_factory=list)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    reopen_conditions: list[ReopenCondition] = Field(default_factory=list)
    status: ClosureStatus = ClosureStatus.PROPOSED
    reason_codes: list[str] = Field(default_factory=list)
    boundary_assessment: "BoundaryAssessment | None" = None


class ReopenSignal(Contract):
    id: str
    closure_id: str
    event_id: str
    trigger_type: ReopenTrigger
    decision: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DissentRecord(Contract):
    """Rebuildable dissent history with conditions for later reactivation."""

    id: str
    target_claim_id: str
    actor_id: str
    timestamp: datetime
    scope: Scope = Field(default_factory=Scope)
    basis: tuple[EvidenceRef, ...] = ()
    relevance_condition: str = ""
    status: DissentStatus = DissentStatus.PRESERVED
    later_outcome: str | None = None
    source_event_ids: tuple[str, ...] = ()


class ActorCursor(Contract):
    """Deterministic observation/exposure cursors, not an IAM or auth model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str
    observation_cursor: str | None = None
    exposure_cursor: str | None = None
    observed_event_ids: tuple[str, ...] = ()
    exposed_event_ids: tuple[str, ...] = ()
    as_of: datetime | None = None


class DistributedView(Contract):
    active_problem_ids: list[str] = Field(default_factory=list)
    relevant_event_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    dissent_ids: list[str] = Field(default_factory=list)
    active_closure_ids: list[str] = Field(default_factory=list)
    reopened_closure_ids: list[str] = Field(default_factory=list)
    unresolved_ids: list[str] = Field(default_factory=list)
    missing_scope: list[str] = Field(default_factory=list)


class RetrievalItem(Contract):
    event_id: str
    score: float
    channel: str
    evidence_kind: EvidenceKind = EvidenceKind.CONTEXT
    selection_reason: str = ""
    estimated_tokens: int = 0


class RetrievalResult(Contract):
    arm: str
    query: str
    candidate_pool_ids: list[str] = Field(default_factory=list)
    selected: list[RetrievalItem] = Field(default_factory=list)
    context_tokens: int = 0
    budget_tokens: int = 0


class ClosureProposal(Contract):
    problem_id: str
    proposition: str
    action: str | None = None
    level: ClosureLevel = ClosureLevel.LOCAL
    scope: Scope | None = None
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    dissent_refs: list[EvidenceRef] = Field(default_factory=list)
    unresolved_refs: list[EvidenceRef] = Field(default_factory=list)
    validation_evidence: list[EvidenceRef] = Field(default_factory=list)
    reopen_conditions: list[ReopenCondition] = Field(default_factory=list)
    boundary_assessment: "BoundaryAssessment | None" = None


class BoundaryAssessment(Contract):
    """A bounded, revisable hypothesis about a proposed TEAM scope.

    This is evidence attached to one proposal, not a global team membership or
    authorization record. Explicit exclusions and uncertainty prevent actor
    count from silently becoming a TEAM label.
    """

    version: str = "boundary.v0.2r"
    included_actor_ids: tuple[str, ...] = ()
    excluded_actor_ids: tuple[str, ...] = ()
    included_source_ids: tuple[str, ...] = ()
    excluded_source_ids: tuple[str, ...] = ()
    included_problem_ids: tuple[str, ...] = ()
    excluded_problem_ids: tuple[str, ...] = ()
    scope_basis: tuple[str, ...] = ()
    counterevidence_refs: tuple[EvidenceRef, ...] = ()
    dissent_refs: tuple[EvidenceRef, ...] = ()
    actor_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    source_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    problem_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    validation_basis: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "BoundaryAssessment":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from.tzinfo is None or self.valid_to.tzinfo is None:
                raise ValueError("BoundaryAssessment validity must be timezone-aware")
            if self.valid_from > self.valid_to:
                raise ValueError("BoundaryAssessment.valid_from cannot be after valid_to")
        return self


class ContextPack(Contract):
    arm: str
    query: str
    system_contract: str
    sections: dict[str, list[str]] = Field(default_factory=dict)
    selected_event_ids: list[str] = Field(default_factory=list)
    context_text: str = ""
    input_tokens: int = 0
    context_hash: str = ""
    sent_to_provider: bool = False
    sent_event_ids: list[str] = Field(default_factory=list)
    actor_cursors: list[ActorCursor] = Field(default_factory=list)
    actor_cursor_manifest: dict[str, Any] = Field(default_factory=dict)
    provider_manifest: dict[str, Any] = Field(default_factory=dict)
    # Reasoning scaffold actually used for this context (G2). Empty means the plain
    # baseline contract, which keeps every earlier artifact's context byte-identical.
    scaffold: str = ""


class ProviderResponse(Contract):
    answer: str
    cited_event_ids: list[str] = Field(default_factory=list)
    output_tokens: int = 0
    provider: str = "mock"
    model: str = ""
    # Provider-reported accounting. ``input_tokens`` stays 0 for providers that do not
    # report usage, and ``usage`` holds the raw accounting block verbatim so a cost claim
    # can be recomputed later. No reasoning text is ever stored here: only counts.
    input_tokens: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    # Wall-clock time of the provider call, for cost/latency comparisons between
    # reasoning scaffolds. 0 means the provider did not measure it.
    latency_ms: int = 0


class EvalCase(Contract):
    case_id: str
    title: str
    query: str
    events: list[RawEvent]
    expected: dict[str, Any] = Field(default_factory=dict)
    transfer: TransferCandidate | None = None
    closure_proposal: ClosureProposal | None = None
    # FR-04: optional explicit decision time. When it is set, a judgement may only
    # rest on evidence dated at or before it, so evidence dated later (for example an
    # E3 future-leak record) cannot change the decision. None keeps the previous
    # behaviour: the latest event timestamp in the corpus.
    decision_time: datetime | None = None


class RunLog(Contract):
    run_id: str
    arm: str
    case_id: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    config_hash: str = ""
    context_hash: str = ""
    output_hash: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    selected_event_ids: list[str] = Field(default_factory=list)
    sent_to_provider: bool = False
    sent_event_ids: list[str] = Field(default_factory=list)
    provider_manifest: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    # Epoch 4 (F-2): widened from `dict[str, float]` so a metric with no denominator can be
    # recorded as UNSCORED instead of being forced into a number. Existing float values are
    # unaffected; a consumer that needs a number must now say what it does with the sentinel.
    metrics: dict[str, float | str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
