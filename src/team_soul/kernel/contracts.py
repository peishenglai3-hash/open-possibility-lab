from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_serializer, model_validator

from ..contracts import ActorCursor, Contract, EvidenceRef
from ..hashing import sha256_json


class RuntimeMode(StrEnum):
    DEMO = "demo"
    REPLAY = "replay"
    PERTURB = "perturb"
    LIVE_PILOT = "live_pilot"


class UserControl(StrEnum):
    NORMAL = "normal"
    SILENT = "silent"
    FREEZE = "freeze"


class PolicyKind(StrEnum):
    RETRIEVAL = "retrieval"
    TRANSFER = "transfer"
    CLOSURE = "closure"
    ACTIVATION = "activation"
    PROBLEM_LINK = "problem_link"
    REASONING = "reasoning"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    SANDBOXED = "sandboxed"
    VALIDATED = "validated"
    HUMAN_APPROVED = "human_approved"
    ACTIVE = "active"
    DISABLED = "disabled"
    CONTESTED = "contested"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ActivationState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    BLOCKED = "BLOCKED"


class EffectOperation(StrEnum):
    ADD = "add"
    SUPERSEDE = "supersede"
    DEACTIVATE = "deactivate"
    ANNOTATE = "annotate"


class SandboxDecision(StrEnum):
    REJECT = "REJECT"
    HUMAN_GATE_REQUIRED = "HUMAN_GATE_REQUIRED"


class ControllerDecision(StrEnum):
    STOP = "STOP"
    CONTINUE = "CONTINUE"
    ESCALATE = "ESCALATE"


class PromotionStatus(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


_ALL_RUNTIME_MODES = frozenset(item.value for item in RuntimeMode)


class RuntimeContext(Contract):
    """Hash-addressed runtime context used by coeffect activation.

    This is a distributed observation scope, not an identity or authorization
    object. Tuple/frozenset fields prevent a caller from changing a context
    after its hash has been derived.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    run_id: str
    timestamp: datetime
    # G0-A: the single decision time of a run, resolved once at construction and frozen
    # (ADR C6/E4). While unset it resolves to ``timestamp`` (U1) and contributes nothing
    # to the hash, so pre-G0-A contexts keep byte-identical identities (ADR E2 pattern).
    effective_time: datetime | None = None
    problem_ids: tuple[str, ...] = ()
    actor_ids: tuple[str, ...] = ()
    actor_cursors: tuple[ActorCursor, ...] = ()
    project_ids: tuple[str, ...] = ()
    time_window: tuple[datetime | None, datetime | None] = (None, None)
    evidence_classes: frozenset[str] = frozenset()
    mode: RuntimeMode
    user_control: UserControl = UserControl.NORMAL
    context_hash: str = ""

    @model_validator(mode="after")
    def ensure_hash(self) -> "RuntimeContext":
        if self.timestamp.tzinfo is None:
            raise ValueError("RuntimeContext.timestamp must be timezone-aware")
        if self.effective_time is not None and self.effective_time.tzinfo is None:
            raise ValueError("RuntimeContext.effective_time must be timezone-aware")
        start, end = self.time_window
        if start is not None and end is not None and start > end:
            raise ValueError("RuntimeContext.time_window start must not be after end")
        computed = sha256_json(self.hash_payload())
        if not self.context_hash:
            object.__setattr__(self, "context_hash", computed)
        elif self.context_hash != computed:
            raise ValueError("context_hash does not match RuntimeContext")
        return self

    @property
    def resolved_effective_time(self) -> datetime:
        """The frozen decision time of this run (ADR C6, U1)."""

        return self.effective_time or self.timestamp

    def hash_payload(self) -> dict[str, Any]:
        """Return the fields covered by context_hash.

        ADR E2 pattern: while ``effective_time`` is unset it is absent from the payload,
        so legacy contexts keep byte-identical hashes; setting it deliberately changes
        the identity of that run.
        """

        start, end = self.time_window
        payload = {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "problem_ids": list(self.problem_ids),
            "actor_ids": list(self.actor_ids),
            "actor_cursors": [
                cursor.model_dump(mode="json")
                for cursor in sorted(self.actor_cursors, key=lambda item: item.actor_id)
            ],
            "project_ids": list(self.project_ids),
            "time_window": [
                start.isoformat() if start is not None else None,
                end.isoformat() if end is not None else None,
            ],
            "evidence_classes": sorted(self.evidence_classes),
            "mode": str(self.mode),
            "user_control": str(self.user_control),
        }
        if self.effective_time is not None:
            payload["effective_time"] = self.effective_time.isoformat()
        return payload


class CoeffectSpec(Contract):
    """Preconditions that determine whether a policy may participate."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    requires_problem: bool = False
    min_evidence: int = Field(default=0, ge=0)
    required_evidence_classes: frozenset[str] = frozenset()
    allow_modes: frozenset[str] = Field(default_factory=lambda: _ALL_RUNTIME_MODES)
    forbid_user_control: frozenset[str] = Field(
        default_factory=lambda: frozenset({UserControl.SILENT.value, UserControl.FREEZE.value})
    )
    max_authority: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_values(self) -> "CoeffectSpec":
        unknown_modes = set(self.allow_modes) - _ALL_RUNTIME_MODES
        if unknown_modes:
            raise ValueError(f"unknown runtime modes in allow_modes: {sorted(unknown_modes)}")
        unknown_controls = set(self.forbid_user_control) - {
            item.value for item in UserControl
        }
        if unknown_controls:
            raise ValueError(
                f"unknown user controls in forbid_user_control: {sorted(unknown_controls)}"
            )
        return self

    @field_serializer(
        "required_evidence_classes",
        "allow_modes",
        "forbid_user_control",
    )
    def serialize_sets(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class PolicySpec(Contract):
    """Versioned policy metadata; lifecycle transitions are append-only."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    policy_id: str
    kind: PolicyKind
    version: str
    implementation: str
    config: dict[str, Any] = Field(default_factory=dict)
    coeffects: CoeffectSpec
    status: PolicyStatus = PolicyStatus.CANDIDATE
    provenance: tuple[EvidenceRef, ...] = Field(min_length=1)
    parent_version: str | None = None


class EffectRecord(Contract):
    """A derived effect record persisted as an append-only raw event."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    effect_id: str
    policy_id: str
    policy_version: str
    context_hash: str
    source_event_ids: tuple[str, ...] = Field(min_length=1)
    target_projection: str
    operation: EffectOperation
    before_hash: str
    after_hash: str
    inverse_spec: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    supersedes_effect_id: str | None = None


class ActivationResult(Contract):
    """Machine-readable activation decision and reasons."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    state: ActivationState
    policy_id: str
    policy_version: str
    context_hash: str
    reasons: tuple[str, ...] = ()


class PolicyDiff(Contract):
    policy_id: str
    from_version: str
    to_version: str
    changed_fields: tuple[str, ...] = ()
    before_hash: str
    after_hash: str


class SandboxReplayReceipt(Contract):
    """Evaluator-issued proof bound to one isolated sandbox replay."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    receipt_id: str
    candidate_policy_id: str
    candidate_policy_version: str
    code_hash: str
    suite_hash: str
    corpus_hash: str
    split_hash: str
    config_hash: str
    evaluator_version: str
    evaluator_hash: str
    run_id: str
    issued_at: datetime
    result_hash: str
    signature: str
    # G0-C optional bindings. Empty means "issued before the binding existed"; a non-empty
    # value must match the transition that consumes the receipt.
    transition_id: str = ""
    base_policy_version: str = ""
    effective_time: str = ""


class SandboxReplayResult(Contract):
    decision: SandboxDecision
    old_policy_id: str
    candidate_policy_id: str
    old_projection_hash: str
    new_projection_hash: str
    disabled_projection_hash: str | None = None
    rebuilt_projection_hash: str | None = None
    raw_history_hash_before: str
    raw_history_hash_after: str
    invariant_regressions: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    receipt: SandboxReplayReceipt | None = None


class TemporalCompositionResult(Contract):
    """Evidence for disable -> replace -> clean-rebuild composition."""

    decision: SandboxDecision
    old_policy_id: str
    candidate_policy_id: str
    old_projection_hash: str
    disabled_projection_hash: str
    candidate_projection_hash: str
    rebuilt_projection_hash: str
    raw_history_hash_before: str
    raw_history_hash_after: str
    invariant_regressions: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class FailureRecord(Contract):
    """Immutable failure/residual evidence passed to the experiment controller."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    failure_id: str
    run_id: str
    stage: str
    context_hash: str
    residual_magnitude: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = ()
    source_event_ids: tuple[str, ...] = ()
    actor_ids: tuple[str, ...] = ()
    evidence_level: str = "E0"
    hard_failure: bool = False
    recoverable: bool = True
    routing_target: Literal[
        "RETRIEVAL", "TRANSFER", "CLOSURE", "POLICY", "HUMAN_REVIEW", "NONE"
    ] = "NONE"
    new_evidence_available: bool = True
    repeat_count: int = Field(default=0, ge=0)
    previous_failure_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExperimentBudget(Contract):
    """Immutable run/token/cost budget snapshot for controlled experiments."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    budget_id: str
    max_runs: int = Field(ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_cost: float | None = Field(default=None, ge=0.0)
    used_runs: int = Field(default=0, ge=0)
    used_tokens: int = Field(default=0, ge=0)
    used_cost: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_usage(self) -> "ExperimentBudget":
        if self.used_runs > self.max_runs:
            raise ValueError("used_runs cannot exceed max_runs")
        if self.max_tokens is not None and self.used_tokens > self.max_tokens:
            raise ValueError("used_tokens cannot exceed max_tokens")
        if self.max_cost is not None and self.used_cost > self.max_cost:
            raise ValueError("used_cost cannot exceed max_cost")
        return self

    def can_consume(
        self,
        *,
        runs: int = 1,
        tokens: int = 0,
        cost: float = 0.0,
    ) -> bool:
        if runs < 0 or tokens < 0 or cost < 0:
            raise ValueError("budget consumption cannot be negative")
        if self.used_runs + runs > self.max_runs:
            return False
        if self.max_tokens is not None and self.used_tokens + tokens > self.max_tokens:
            return False
        if self.max_cost is not None and self.used_cost + cost > self.max_cost:
            return False
        return True

    def consume(
        self,
        *,
        runs: int = 1,
        tokens: int = 0,
        cost: float = 0.0,
    ) -> "ExperimentBudget":
        if not self.can_consume(runs=runs, tokens=tokens, cost=cost):
            raise ValueError("experiment budget exhausted")
        return self.model_copy(
            update={
                "used_runs": self.used_runs + runs,
                "used_tokens": self.used_tokens + tokens,
                "used_cost": self.used_cost + cost,
            }
        )

    @property
    def exhausted(self) -> bool:
        if self.used_runs >= self.max_runs:
            return True
        if self.max_tokens is not None and self.used_tokens >= self.max_tokens:
            return True
        if self.max_cost is not None and self.used_cost >= self.max_cost:
            return True
        return False


class ExperimentDecision(Contract):
    """Deterministic controller output; it does not execute or promote work."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    decision: ControllerDecision
    budget_id: str
    failure_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    remaining_runs: int
    remaining_tokens: int | None = None
    remaining_cost: float | None = None
    routing_target: str = "NONE"


class ExperimentRun(Contract):
    """Immutable experiment receipt; evidence, not a policy state source."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    run_id: str
    hypothesis: str
    baseline_policy: str
    candidate_policy: str
    scenario_ids: tuple[str, ...]
    model_configuration: dict[str, Any] = Field(default_factory=dict)
    heldout_split_hash: str
    metrics: dict[str, float] = Field(default_factory=dict)
    failure_ids: tuple[str, ...] = ()
    artifacts: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> "ExperimentRun":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("experiment timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("experiment finished_at cannot precede started_at")
        return self


class PromotionDecision(Contract):
    """Dry-run recommendation; it can never activate or auto-promote a policy."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    decision: PromotionStatus
    experiment_run_id: str
    invariant_regressions: tuple[str, ...] = ()
    hard_gate_regressions: tuple[str, ...] = ()
    pareto_improvements: tuple[str, ...] = ()
    heldout_results: dict[str, Any] = Field(default_factory=dict)
    human_gate_required: Literal[True] = True
    auto_promote: Literal[False] = False
    reason_codes: tuple[str, ...] = ()


class CandidatePolicy(Contract):
    """A lifecycle view over PolicySpec, not a second policy truth source."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    policy: PolicySpec
    context_hash: str = ""
    selection_reasons: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_policy(
        cls,
        policy: PolicySpec,
        *,
        context_hash: str = "",
        selection_reasons: tuple[str, ...] = (),
    ) -> "CandidatePolicy":
        return cls(
            policy=policy,
            context_hash=context_hash,
            selection_reasons=selection_reasons,
        )

    @property
    def policy_id(self) -> str:
        return self.policy.policy_id

    @property
    def version(self) -> str:
        return self.policy.version

    @property
    def parent_version(self) -> str | None:
        return self.policy.parent_version

    @property
    def status(self) -> PolicyStatus:
        return PolicyStatus(self.policy.status)

    @property
    def requires_human_gate(self) -> bool:
        return self.status == PolicyStatus.HUMAN_APPROVED
