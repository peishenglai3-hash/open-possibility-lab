from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..config import RuntimeConfig
from ..contracts import (
    ClosureProposal,
    OperationalClosure,
    Relation,
    RetrievalResult,
    TransferCandidate,
)
from ..operators import check_transfer, evaluate_closure
from ..projection import load_relations, rebuild_projection
from ..retrieval import retrieve as deterministic_retrieve
from ..storage import Store
from .activation import resolve_activation
from .contracts import ActivationResult, PolicyKind, PolicySpec, PolicyStatus, RuntimeContext


@runtime_checkable
class RetrievalPolicy(Protocol):
    def execute(
        self, store: Store, query: str, arm: str, config: RuntimeConfig
    ) -> RetrievalResult: ...


@runtime_checkable
class TransferPolicy(Protocol):
    def evaluate(self, candidate: TransferCandidate) -> TransferCandidate: ...


@runtime_checkable
class ClosurePolicy(Protocol):
    def evaluate(
        self,
        proposal: ClosureProposal,
        store: Store,
        config: RuntimeConfig,
        *,
        as_of: datetime | None = None,
    ) -> OperationalClosure: ...


@runtime_checkable
class ProblemLinkPolicy(Protocol):
    def rebuild(self, store: Store, *, as_of: datetime | None = None) -> tuple[Relation, ...]: ...


@runtime_checkable
class ActivationPolicy(Protocol):
    def resolve(self, policy: PolicySpec, context: RuntimeContext) -> ActivationResult: ...


@runtime_checkable
class BoundaryPolicy(Protocol):
    """Future replaceable boundary assessment seam; not bound in V0.2-R."""

    def assess(self, proposal: ClosureProposal, store: Store, config: RuntimeConfig) -> Any: ...


@runtime_checkable
class ReasoningPolicy(Protocol):
    """Future trace/scoring seam; it must not persist private chain-of-thought."""

    def trace(self, request: Any) -> Any: ...


@runtime_checkable
class GenerativePolicy(Protocol):
    """Future speculative-candidate seam; no direct fact/closure promotion."""

    def propose(self, request: Any) -> Any: ...


class DeterministicRetrievalReference:
    def execute(
        self, store: Store, query: str, arm: str, config: RuntimeConfig
    ) -> RetrievalResult:
        return deterministic_retrieve(store, query, arm, config)


class BindingTransferGuardReference:
    def evaluate(self, candidate: TransferCandidate) -> TransferCandidate:
        return check_transfer(candidate)


class DeterministicClosureGateReference:
    def evaluate(
        self,
        proposal: ClosureProposal,
        store: Store,
        config: RuntimeConfig,
        *,
        as_of: datetime | None = None,
    ) -> OperationalClosure:
        return evaluate_closure(proposal, store, config, as_of=as_of)


class ProjectionProblemLinkReference:
    def rebuild(self, store: Store, *, as_of: datetime | None = None) -> tuple[Relation, ...]:
        rebuild_projection(store, as_of=as_of)
        return tuple(load_relations(store))


class CoeffectActivationReference:
    def resolve(self, policy: PolicySpec, context: RuntimeContext) -> ActivationResult:
        return resolve_activation(policy, context)


@dataclass(frozen=True)
class PolicyBundle:
    """The replaceable policy bundle consumed by the evaluation runtime.

    A bundle is an execution seam, not a second lifecycle store. PolicySpec
    status and provenance still live in PolicyRegistry; this object only binds
    the implementation selected for one run.
    """

    retrieval: RetrievalPolicy
    transfer: TransferPolicy
    closure: ClosurePolicy
    problem_link: ProblemLinkPolicy
    activation: ActivationPolicy


def deterministic_policy_bundle() -> PolicyBundle:
    return PolicyBundle(
        retrieval=DeterministicRetrievalReference(),
        transfer=BindingTransferGuardReference(),
        closure=DeterministicClosureGateReference(),
        problem_link=ProjectionProblemLinkReference(),
        activation=CoeffectActivationReference(),
    )


class PolicyRuntime:
    """Resolve active registered policies into the executable policy bundle."""

    def __init__(
        self,
        registry,
        *,
        implementations: dict[str, object],
        fallback: PolicyBundle | None = None,
    ) -> None:
        self.registry = registry
        self.implementations = dict(implementations)
        self.fallback = fallback or deterministic_policy_bundle()

    def active_policy(self, kind: PolicyKind) -> PolicySpec | None:
        active = [
            policy
            for policy in self.registry.list()
            if PolicyKind(policy.kind) == kind
            and PolicyStatus(policy.status) == PolicyStatus.ACTIVE
        ]
        if len(active) > 1:
            raise ValueError(f"multiple active policies for {kind.value}")
        return active[0] if active else None

    def resolve_component(self, kind: PolicyKind) -> object:
        policy = self.active_policy(kind)
        if policy is None:
            return _fallback_component(self.fallback, kind)
        try:
            return self.implementations[policy.implementation]
        except KeyError as exc:
            raise KeyError(
                f"no executable implementation registered for {policy.implementation!r}"
            ) from exc

    def bundle(self) -> PolicyBundle:
        return PolicyBundle(
            retrieval=self.resolve_component(PolicyKind.RETRIEVAL),
            transfer=self.resolve_component(PolicyKind.TRANSFER),
            closure=self.resolve_component(PolicyKind.CLOSURE),
            problem_link=self.resolve_component(PolicyKind.PROBLEM_LINK),
            activation=self.resolve_component(PolicyKind.ACTIVATION),
        )

    def manifest(self) -> dict[str, object]:
        policies = {
            kind.value: (
                {
                    "policy_id": policy.policy_id,
                    "version": policy.version,
                    "implementation": policy.implementation,
                }
                if (policy := self.active_policy(kind)) is not None
                else None
            )
            for kind in PolicyKind
        }
        return {"resolution": "active_registry_policy_or_deterministic_fallback", "policies": policies}


def _fallback_component(bundle: PolicyBundle, kind: PolicyKind) -> object:
    return {
        PolicyKind.RETRIEVAL: bundle.retrieval,
        PolicyKind.TRANSFER: bundle.transfer,
        PolicyKind.CLOSURE: bundle.closure,
        PolicyKind.PROBLEM_LINK: bundle.problem_link,
        PolicyKind.ACTIVATION: bundle.activation,
    }[kind]


POLICY_SEAM_CATALOG = (
    {
        "name": "RetrievalPolicy",
        "protocol": "team_soul.kernel.policies.RetrievalPolicy",
        "reference": "DeterministicRetrievalReference",
        "mechanism": "BASE/RAG/SOUL retrieval with explicit candidate evidence",
    },
    {
        "name": "TransferPolicy",
        "protocol": "team_soul.kernel.policies.TransferPolicy",
        "reference": "BindingTransferGuardReference",
        "mechanism": "external veto and forbidden-condition hard rejection",
    },
    {
        "name": "ClosurePolicy",
        "protocol": "team_soul.kernel.policies.ClosurePolicy",
        "reference": "DeterministicClosureGateReference",
        "mechanism": "provenance/scope/reopen checks; UNKNOWN and NO_CLOSURE remain valid",
    },
    {
        "name": "ProblemLinkPolicy",
        "protocol": "team_soul.kernel.policies.ProblemLinkPolicy",
        "reference": "ProjectionProblemLinkReference",
        "mechanism": "history-derived, rebuildable relation projection",
    },
    {
        "name": "ActivationPolicy",
        "protocol": "team_soul.kernel.policies.ActivationPolicy",
        "reference": "CoeffectActivationReference",
        "mechanism": "deterministic RuntimeContext/CoeffectSpec gate",
    },
)


FUTURE_POLICY_SEAM_CATALOG = (
    {
        "name": "BoundaryPolicy",
        "protocol": "team_soul.kernel.policies.BoundaryPolicy",
        "reference": None,
        "mechanism": "future bounded actor/source/problem boundary assessment; not bound in V0.2-R",
    },
    {
        "name": "ReasoningPolicy",
        "protocol": "team_soul.kernel.policies.ReasoningPolicy",
        "reference": None,
        "mechanism": "future auditable reasoning trace/scorer seam; no private chain-of-thought",
    },
    {
        "name": "GenerativePolicy",
        "protocol": "team_soul.kernel.policies.GenerativePolicy",
        "reference": None,
        "mechanism": "future speculative candidate generation; no direct fact/closure promotion",
    },
)


def policy_catalog_markdown() -> str:
    lines = [
        "# Team Soul V0.2 Policy Registry and Learnable Seams",
        "",
        "Generated from the code-level deterministic reference catalog. These",
        "are replaceable interfaces, not evidence that a learned policy exists.",
        "No neural policy is trained in V0.2. Production `auto_promote` is false.",
        "",
        "| Policy seam | Protocol | Deterministic reference | Bound behavior |",
        "|---|---|---|---|",
    ]
    for item in POLICY_SEAM_CATALOG:
        lines.append(
            f"| {item['name']} | `{item['protocol']}` | `{item['reference']}` | {item['mechanism']} |"
        )
    lines.extend(
        [
            "",
            "## Pre-V0.3 seams (declared, not executed)",
            "",
            "These interfaces preserve a replaceable deterministic → heuristic → learned path "
            "without binding V0.3 behavior or changing the RawEvent contract.",
            "",
            "| Future seam | Protocol | Reference | Boundary |",
            "|---|---|---|---|",
        ]
    )
    for item in FUTURE_POLICY_SEAM_CATALOG:
        lines.append(
            f"| {item['name']} | `{item['protocol']}` | `not bound` | {item['mechanism']} |"
        )
    lines.extend(
        [
            "",
            "## Lifecycle and evidence boundary",
            "",
            "`PolicyRegistry` stores explicit append-only PolicySpec lifecycle events.",
            "The candidate path is `DRAFT -> SANDBOXED -> VALIDATED -> HUMAN_APPROVED -> ACTIVE`;",
            "reject/contest/revoke and disable/restore/fork remain explicit alternatives.",
            "The SANDBOXED transition requires a passing SandboxReplayResult with unchanged",
            "raw history and a matching clean rebuild. Human approval and activation both",
            "require a human actor. A policy is not active merely because a deterministic reference exists.",
            "The fixture Demo uses an isolated in-memory store and has no persisted",
            "user policy registrations. Candidate/held-out/promotion receipts are",
            "evaluation artifacts; they cannot mutate raw history or activate a policy.",
            "",
            "Future supervision is represented by the append-only pilot feedback",
            "signal tuple: hashed/scope-bounded `context`, `candidate_action`,",
            "`human_correction`, `accepted_state`, and `downstream_outcome`, with",
            "evidence level. Raw context snippets are not stored in that tuple.",
            "V0.2 does not train a scorer from these fields.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "ActivationPolicy",
    "BoundaryPolicy",
    "BindingTransferGuardReference",
    "ClosurePolicy",
    "CoeffectActivationReference",
    "DeterministicClosureGateReference",
    "DeterministicRetrievalReference",
    "POLICY_SEAM_CATALOG",
    "FUTURE_POLICY_SEAM_CATALOG",
    "ProblemLinkPolicy",
    "ProjectionProblemLinkReference",
    "RetrievalPolicy",
    "ReasoningPolicy",
    "TransferPolicy",
    "GenerativePolicy",
    "PolicyBundle",
    "PolicyRuntime",
    "deterministic_policy_bundle",
    "policy_catalog_markdown",
]
