from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from .config import RuntimeConfig
from .contracts import (
    ActorType,
    BoundaryAssessment,
    ClosureProposal,
    ClosureLevel,
    ClosureStatus,
    Condition,
    EvidenceKind,
    OperationalClosure,
    ReopenSignal,
    ReopenTrigger,
    RawEvent,
    Scope,
    TransferCandidate,
    TransferDecision,
)
from .hashing import canonical_json, sha256_json
from .projection import load_closures, rebuild_projection
from .temporal import is_visible, visible_events
from .storage import Store


def check_transfer(candidate: TransferCandidate) -> TransferCandidate:
    """Deterministic transfer gate; similarity never bypasses these checks."""
    decision = TransferDecision.UNVERIFIED
    reason_codes: list[str] = []
    binding_veto = candidate.external_veto or bool(candidate.veto_evidence_refs) or any(
        ref.evidence_kind == EvidenceKind.VETO for ref in candidate.evidence_refs
    )
    if binding_veto:
        decision = TransferDecision.REJECT
        reason_codes.append("EXTERNAL_VETO_BINDING")
    if candidate.forbidden_conditions:
        decision = TransferDecision.REJECT
        reason_codes.append("FORBIDDEN_CONDITION")
    elif decision != TransferDecision.REJECT and any(
        _state(condition) in {"CHANGED", "FORBIDDEN"} for condition in candidate.changed_conditions
    ):
        decision = TransferDecision.REJECT
        reason_codes.append("CHANGED_CONDITION_BLOCKS_TRANSFER")
    elif decision != TransferDecision.REJECT and (
        candidate.missing_conditions
        or any(
            _state(condition) in {"MISSING", "UNKNOWN"}
            for condition in candidate.required_conditions
        )
    ):
        decision = TransferDecision.HOLD
        reason_codes.append("MISSING_REQUIRED_CONDITION")
    elif decision != TransferDecision.REJECT and not candidate.evidence_refs:
        decision = TransferDecision.UNVERIFIED
        reason_codes.append("NO_PROVENANCE")
    elif decision != TransferDecision.REJECT and not candidate.shared_conditions:
        decision = TransferDecision.HOLD
        reason_codes.append("NO_SHARED_CONDITION")
    elif decision != TransferDecision.REJECT:
        decision = TransferDecision.CANDIDATE
        reason_codes.append("SHARED_AND_REQUIRED_CONDITIONS_PRESENT")
    return candidate.model_copy(update={"decision": decision, "reason_codes": reason_codes})


def evaluate_closure(
    proposal: ClosureProposal,
    store: Store,
    config: RuntimeConfig,
    *,
    as_of: datetime | None = None,
) -> OperationalClosure:
    """Evaluate a closure proposal.

    FR-04: a TEAM promotion is time-bound. When as_of is supplied the judgement may
    only rest on evidence dated at or before it; otherwise the decision time is the
    latest timestamp among the closure own cited evidence.
    """
    reasons: list[str] = []
    non_blocking_notes: list[str] = []
    scope = proposal.scope or Scope(problem_ids=[proposal.problem_id])
    evaluated_level = proposal.level
    all_refs = (
        proposal.supporting_evidence
        + proposal.dissent_refs
        + proposal.unresolved_refs
        + proposal.validation_evidence
    )
    known_events = {ref.source_event_id: store.get_event(ref.source_event_id) for ref in all_refs}
    if as_of is not None:
        # G0-A: an evidence event that is not yet visible at the decision time counts as
        # missing (fail-closed), so no operator can read outside the cognitive history.
        known_events = {
            event_id: event
            for event_id, event in known_events.items()
            if event is not None and is_visible(event, as_of)
        }
    missing_provenance = [event_id for event_id, event in known_events.items() if event is None]
    if missing_provenance:
        reasons.append("UNKNOWN_EVIDENCE_EVENT")
    if config.closure_require_scope and (
        not scope.label or scope.label == "UNSCOPED"
    ):
        reasons.append("MISSING_SCOPE")
    if config.closure_require_provenance and not (
        proposal.supporting_evidence or proposal.validation_evidence
    ):
        reasons.append("MISSING_PROVENANCE")
    if config.closure_require_reopen_condition and not proposal.reopen_conditions:
        reasons.append("MISSING_REOPEN_CONDITION")
    if not proposal.validation_evidence:
        reasons.append("MISSING_VALIDATION_EVIDENCE")
    if proposal.unresolved_refs:
        reasons.append("UNRESOLVED_EVIDENCE_PRESENT")
    if config.hard_veto_blocks and any(
        ref.evidence_kind == EvidenceKind.VETO for ref in proposal.dissent_refs
    ):
        reasons.append("HARD_VETO")
    if proposal.level == "TEAM":
        team_reasons = _team_boundary_reasons(proposal, scope, store, config, as_of=as_of)
        if team_reasons:
            evaluated_level = ClosureLevel.PROJECT if scope.project_ids else ClosureLevel.LOCAL
            fallback_label = (
                f"project:{scope.project_ids[0]}"
                if scope.project_ids
                else "LOCAL"
            )
            scope = scope.model_copy(update={"label": fallback_label})
            non_blocking_notes.extend(["TEAM_SCOPE_DOWNGRADED", *team_reasons])
    status = ClosureStatus.ACTIVE if not reasons else ClosureStatus.NO_CLOSURE
    closure_id = f"closure:{sha256_json(proposal.model_dump(mode='json'))[:20]}"
    return OperationalClosure(
        id=closure_id,
        problem_id=proposal.problem_id,
        proposition=proposal.proposition,
        action=proposal.action,
        level=evaluated_level,
        scope=scope,
        supporting_evidence=proposal.supporting_evidence,
        dissent_refs=proposal.dissent_refs,
        unresolved_refs=proposal.unresolved_refs,
        validation_evidence=proposal.validation_evidence,
        status=status,
        reason_codes=[*reasons, *non_blocking_notes],
        reopen_conditions=proposal.reopen_conditions,
        boundary_assessment=proposal.boundary_assessment,
    )


def _team_boundary_reasons(
    proposal: ClosureProposal,
    scope: Scope,
    store: Store,
    config: RuntimeConfig,
    *,
    as_of: datetime | None = None,
) -> list[str]:
    """Validate a TEAM promotion as a bounded hypothesis, not a count rule.

    FR-04: the coverage universe is the closure own cited evidence, never the whole
    library. A TEAM boundary therefore cannot be satisfied by unrelated or
    future-dated events, and the proposed problem id cannot whitelist itself.
    """

    assessment = proposal.boundary_assessment
    if assessment is None:
        return [
            "TEAM_BOUNDARY_ASSESSMENT_REQUIRED",
            "INSUFFICIENT_ACTOR_COVERAGE_FOR_TEAM",
        ]

    cited_refs = [
        *proposal.supporting_evidence,
        *proposal.dissent_refs,
        *proposal.unresolved_refs,
        *proposal.validation_evidence,
    ]
    cited_ids = {ref.source_event_id for ref in cited_refs}
    basis_events = []
    after_as_of: list[str] = []
    for ref in cited_refs:
        event = store.get_event(ref.source_event_id)
        if event is None:
            continue
        if as_of is not None and not is_visible(event, as_of):
            after_as_of.append(ref.source_event_id)
            continue
        basis_events.append(event)

    # FR-04: coverage is verified against the problem-scoped raw history (union the
    # closure own cited evidence), bounded by the decision time when one is given.
    # Unrelated events elsewhere in the library can no longer satisfy a boundary claim.
    universe = [
        event
        for event in visible_events(store.list_events(), as_of)
        if event.id in cited_ids or proposal.problem_id in event.problem_hints
    ]

    known_events = {event.id: event for event in universe}
    known_actor_ids = {event.actor_id for event in universe}
    known_source_ids = {
        value
        for event in universe
        for value in (event.id, event.source_locator)
    }
    known_problem_ids = {
        hint
        for event in universe
        for hint in event.problem_hints
    }
    for event in universe:
        curated = event.metadata.get("curated", {})
        if isinstance(curated, dict):
            known_problem_ids.update(
                str(item.get("id"))
                for item in curated.get("problems", [])
                if isinstance(item, dict) and item.get("id")
            )

    reasons: list[str] = []
    if after_as_of:
        reasons.append("TEAM_EVIDENCE_AFTER_AS_OF")
    if as_of is not None:
        if assessment.valid_from is not None and assessment.valid_from > as_of:
            reasons.append("TEAM_BOUNDARY_NOT_YET_VALID")
        if assessment.valid_to is not None and as_of > assessment.valid_to:
            reasons.append("TEAM_BOUNDARY_EXPIRED")
    else:
        latest_basis = max((event.timestamp for event in basis_events), default=None)
        if (
            latest_basis is not None
            and assessment.valid_to is not None
            and latest_basis > assessment.valid_to
        ):
            reasons.append("TEAM_BOUNDARY_EXPIRED")
    if len(set(assessment.included_actor_ids)) < max(
        int(config.team_level_min_actor_coverage), 0
    ):
        reasons.append("TEAM_ACTOR_COVERAGE_BELOW_MINIMUM")
    if not scope.label.lower().startswith("team:"):
        reasons.append("TEAM_SCOPE_LABEL_REQUIRED")
    if not scope.scope_basis or not assessment.scope_basis:
        reasons.append("MISSING_TEAM_SCOPE_BASIS")
    if not scope.actor_coverage or not assessment.included_actor_ids:
        reasons.append("MISSING_TEAM_ACTOR_COVERAGE")
    elif set(scope.actor_coverage) != set(assessment.included_actor_ids):
        reasons.append("TEAM_ACTOR_COVERAGE_BASIS_MISMATCH")
    elif not set(assessment.included_actor_ids).issubset(known_actor_ids):
        reasons.append("TEAM_ACTOR_COVERAGE_UNVERIFIED")
    if not scope.source_coverage or not assessment.included_source_ids:
        reasons.append("MISSING_TEAM_SOURCE_COVERAGE")
    elif set(scope.source_coverage) != set(assessment.included_source_ids):
        reasons.append("TEAM_SOURCE_COVERAGE_BASIS_MISMATCH")
    elif not set(assessment.included_source_ids).issubset(known_source_ids):
        reasons.append("TEAM_SOURCE_COVERAGE_UNVERIFIED")
    if not assessment.included_problem_ids or proposal.problem_id not in assessment.included_problem_ids:
        reasons.append("TEAM_PROBLEM_SCOPE_UNVERIFIED")
    elif not set(assessment.included_problem_ids).issubset(known_problem_ids):
        reasons.append("TEAM_PROBLEM_COVERAGE_UNVERIFIED")
    if set(assessment.included_actor_ids).intersection(assessment.excluded_actor_ids):
        reasons.append("TEAM_ACTOR_BOUNDARY_OVERLAP")
    if set(assessment.included_source_ids).intersection(assessment.excluded_source_ids):
        reasons.append("TEAM_SOURCE_BOUNDARY_OVERLAP")
    if set(assessment.included_problem_ids).intersection(assessment.excluded_problem_ids):
        reasons.append("TEAM_PROBLEM_BOUNDARY_OVERLAP")

    counterevidence = [
        *proposal.dissent_refs,
        *assessment.counterevidence_refs,
        *assessment.dissent_refs,
    ]
    if not counterevidence and not scope.counterevidence_refs:
        reasons.append("MISSING_TEAM_COUNTEREVIDENCE_OR_DISSENT")
    if any(ref.source_event_id not in known_events for ref in counterevidence):
        reasons.append("TEAM_COUNTEREVIDENCE_UNVERIFIED")
    if counterevidence and not any(
        ref.evidence_kind
        in {EvidenceKind.CONTRADICT, EvidenceKind.DISSENT, EvidenceKind.VETO, EvidenceKind.OUTCOME}
        for ref in counterevidence
    ):
        reasons.append("TEAM_COUNTEREVIDENCE_KIND_UNVERIFIED")
    if not scope.validation_basis or not assessment.validation_basis:
        reasons.append("MISSING_TEAM_VALIDATION_BASIS")
    if not proposal.validation_evidence:
        reasons.append("TEAM_VALIDATION_BASIS_WITHOUT_EVIDENCE")
    if assessment.actor_coverage <= 0.0:
        reasons.append("TEAM_ACTOR_COVERAGE_UNDECLARED")
    if assessment.source_coverage <= 0.0:
        reasons.append("TEAM_SOURCE_COVERAGE_UNDECLARED")
    if assessment.problem_coverage <= 0.0:
        reasons.append("TEAM_PROBLEM_COVERAGE_UNDECLARED")
    if assessment.uncertainty == "UNKNOWN":
        reasons.append("TEAM_BOUNDARY_UNCERTAINTY_UNKNOWN")
    if not assessment.version:
        reasons.append("TEAM_BOUNDARY_VERSION_MISSING")
    return sorted(set(reasons))


def check_reopen(
    closure: OperationalClosure,
    events: Iterable,
    *,
    effective_time: datetime,
) -> tuple[OperationalClosure, list[ReopenSignal]]:
    """Check reopen triggers against the cognitive history (G0-A, FR-16).

    ADR U1: only events visible at the frozen decision time may trigger a reopen, so a
    future-dated event (or one that was not yet knowable) can no longer penetrate this
    path. The decision time is a required argument: an unscoped trigger scan is exactly
    the bypass FR-16 recorded.
    """

    if closure.status != ClosureStatus.ACTIVE:
        return closure, []
    from .temporal import visible_events

    signals: list[ReopenSignal] = []
    for event in visible_events(list(events), effective_time):
        metadata = event.metadata
        closure_ids = set(metadata.get("reopen_closure_ids", []))
        problem_ids = set(metadata.get("reopen_problem_ids", []))
        if closure.id not in closure_ids and closure.problem_id not in problem_ids:
            continue
        raw_trigger = metadata.get("reopen_trigger", ReopenTrigger.EXPLICIT_REOPEN_CONDITION)
        try:
            trigger = ReopenTrigger(raw_trigger)
        except ValueError:
            trigger = ReopenTrigger.EXPLICIT_REOPEN_CONDITION
        signal = ReopenSignal(
            id=f"reopen:{sha256_json({'closure_id': closure.id, 'event_id': event.id, 'trigger': str(trigger)})[:20]}",
            closure_id=closure.id,
            event_id=event.id,
            trigger_type=trigger,
            decision="REOPEN",
        )
        signals.append(signal)
    if not signals:
        return closure, []
    reopened = closure.model_copy(
        update={
            "status": ClosureStatus.REOPENED,
            "reason_codes": [*closure.reason_codes, "REOPEN_SIGNAL_DETECTED"],
        }
    )
    return reopened, signals


def persist_closure_proposal(
    proposal: ClosureProposal,
    store: Store,
    config: RuntimeConfig,
) -> OperationalClosure:
    closure = evaluate_closure(proposal, store, config).model_copy(
        update={"status": ClosureStatus.PROPOSED}
    )
    store.append_event(_closure_event("proposed", closure))
    rebuild_projection(store)
    return closure


def validate_closure(
    proposal: ClosureProposal,
    store: Store,
    config: RuntimeConfig,
) -> OperationalClosure:
    rebuild_projection(store)
    proposed_id = f"closure:{sha256_json(proposal.model_dump(mode='json'))[:20]}"
    existing = next((item for item in load_closures(store) if item.id == proposed_id), None)
    if existing is not None and existing.status == ClosureStatus.REVOKED:
        raise ValueError(
            "revoked closure versions are terminal; create a new proposal/version for revalidation"
        )
    closure = evaluate_closure(proposal, store, config)
    store.append_event(_closure_event("validated", closure))
    rebuild_projection(store)
    return closure


def persist_reopen(
    closure_id: str,
    store: Store,
    events: Iterable[RawEvent],
    *,
    effective_time: datetime,
) -> tuple[OperationalClosure, list[ReopenSignal]]:
    trigger_events = list(events)
    store.append_events(trigger_events)
    rebuild_projection(store)
    closure = next((item for item in load_closures(store) if item.id == closure_id), None)
    if closure is None:
        raise KeyError(f"closure not found: {closure_id}")
    reopened, signals = check_reopen(closure, trigger_events, effective_time=effective_time)
    for signal in signals:
        store.append_event(_closure_event("reopened", reopened, signal=signal))
    if signals:
        rebuild_projection(store)
    return reopened, signals


def revoke_closure(store: Store, closure_id: str, reason: str) -> OperationalClosure:
    rebuild_projection(store)
    closure = next((item for item in load_closures(store) if item.id == closure_id), None)
    if closure is None:
        raise KeyError(f"closure not found: {closure_id}")
    revoked = closure.model_copy(
        update={
            "status": ClosureStatus.REVOKED,
            "reason_codes": [*closure.reason_codes, "REVOKED", reason],
        }
    )
    store.append_event(_closure_event("revoked", revoked, reason=reason))
    rebuild_projection(store)
    return revoked


def _closure_event(
    event_kind: str,
    closure: OperationalClosure,
    *,
    signal: ReopenSignal | None = None,
    reason: str = "",
) -> RawEvent:
    payload: dict[str, object] = {
        "event_kind": event_kind,
        "closure": closure.model_dump(mode="json"),
    }
    if signal is not None:
        payload["signal"] = signal.model_dump(mode="json")
    if reason:
        payload["reason"] = reason
    raw_content = canonical_json(payload)
    event_id = f"evt:closure:{sha256_json(payload)[:24]}"
    return RawEvent(
        id=event_id,
        timestamp=datetime.now(timezone.utc),
        actor_id="team-soul-runtime",
        actor_type=ActorType.TOOL,
        source_type="team_soul.closure",
        source_locator=f"closure:{closure.id}:{event_kind}",
        raw_content=raw_content,
        project_hints=list(closure.scope.project_ids),
        problem_hints=[closure.problem_id],
        metadata={
            "event_kind": "closure_lifecycle",
            "closure_event": payload,
            "exposed_actor_ids": ["team-soul-runtime"],
        },
    )


def _state(condition: Condition) -> str:
    return str(condition.state)
