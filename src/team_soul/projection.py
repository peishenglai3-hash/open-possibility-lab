from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import (
    ActorCursor,
    Claim,
    ClosureStatus,
    DissentRecord,
    DissentStatus,
    EvidenceKind,
    EvidenceRef,
    OperationalClosure,
    ProblemStatus,
    ProblemThread,
    RawEvent,
    Relation,
    ReopenSignal,
    Scope,
)
from .hashing import sha256_json
from .storage import Store


def _append_unique(items: list[str], values: list[str]) -> list[str]:
    seen = set(items)
    for value in values:
        if value and value not in seen:
            items.append(value)
            seen.add(value)
    return items


def _merge_problem(current: ProblemThread, incoming: ProblemThread) -> ProblemThread:
    current.source_event_ids = _append_unique(current.source_event_ids, incoming.source_event_ids)
    current.parent_problem_ids = _append_unique(current.parent_problem_ids, incoming.parent_problem_ids)
    current.related_problem_ids = _append_unique(current.related_problem_ids, incoming.related_problem_ids)
    current.provenance.extend(
        item for item in incoming.provenance if item not in current.provenance
    )
    if current.status == ProblemStatus.HYPOTHESIZED and incoming.status != ProblemStatus.HYPOTHESIZED:
        current.status = incoming.status
    if current.title == current.id and incoming.title:
        current.title = incoming.title
    if current.closed_at is None:
        current.closed_at = incoming.closed_at
    if current.scope.label == "UNSCOPED" and incoming.scope.label != "UNSCOPED":
        current.scope = incoming.scope
    return current


def _merge_claim(current: Claim, incoming: Claim) -> Claim:
    current.source_event_ids = _append_unique(current.source_event_ids, incoming.source_event_ids)
    current.problem_ids = _append_unique(current.problem_ids, incoming.problem_ids)
    if incoming.confidence > current.confidence:
        current.confidence = incoming.confidence
    if current.status == "HYPOTHESIZED" and incoming.status != "HYPOTHESIZED":
        current.status = incoming.status
    return current


def _event_scope(event: RawEvent, problem_id: str) -> Scope:
    return Scope(
        label=event.project_hints[0] if event.project_hints else "EVENT_LOCAL",
        project_ids=list(event.project_hints),
        problem_ids=[problem_id],
    )


def _problem_id(hint: str) -> str:
    if hint.startswith("problem:"):
        return hint
    return f"problem:{sha256_json({'hint': hint})[:16]}"


def _curated(event: RawEvent) -> dict[str, Any]:
    value = event.metadata.get("curated", {})
    return value if isinstance(value, dict) else {}


def _closure_evidence(closure: OperationalClosure) -> list[EvidenceRef]:
    return [
        *closure.supporting_evidence,
        *closure.dissent_refs,
        *closure.unresolved_refs,
        *closure.validation_evidence,
    ]


def _admit_closure(
    closures: dict[str, OperationalClosure],
    closure: OperationalClosure,
    event_ids: set[str],
) -> None:
    """Admit closure state carried by raw metadata without letting it rewrite history.

    FR-02: closure lifecycle state legitimately travels in raw event metadata, but a
    REVOKED version is terminal (a later payload may not reinstate it) and derived
    state must carry provenance (referenced evidence must exist in raw history).
    """

    missing = sorted(
        {
            ref.source_event_id
            for ref in _closure_evidence(closure)
            if ref.source_event_id and ref.source_event_id not in event_ids
        }
    )
    if missing:
        raise ValueError(
            f"closure {closure.id!r} refers to evidence that is not present in raw event "
            "history: " + ", ".join(missing)
        )
    existing = closures.get(closure.id)
    if existing is not None and existing.status == ClosureStatus.REVOKED:
        return
    _require_team_boundary(closure)
    closures[closure.id] = closure


_ACTIVE_CLOSURE_STATES = (ClosureStatus.ACTIVE, ClosureStatus.REOPENED)


def _require_team_boundary(closure: OperationalClosure) -> None:
    """Fail closed when an active TEAM closure did not pass a BoundaryAssessment.

    FR-02 follow-up (ADV-A6): the provenance check stops dangling evidence, but a
    curated payload could still mint an ACTIVE TEAM closure whose evidence exists
    and yet never passed evaluate_closure / _team_boundary_reasons.  A TEAM claim
    is a guarded scope promotion; the reducer must not manufacture one from raw
    metadata alone.  Non-TEAM closures and non-active states are unaffected.
    """

    if str(closure.level) != "TEAM" or closure.status not in _ACTIVE_CLOSURE_STATES:
        return
    assessment = closure.boundary_assessment
    missing: list[str] = []
    if assessment is None:
        missing.append("TEAM_BOUNDARY_ASSESSMENT_REQUIRED")
    else:
        if not str(closure.scope.label).lower().startswith("team:"):
            missing.append("TEAM_SCOPE_LABEL_REQUIRED")
        if not assessment.included_actor_ids:
            missing.append("MISSING_TEAM_ACTOR_COVERAGE")
        if not assessment.included_source_ids:
            missing.append("MISSING_TEAM_SOURCE_COVERAGE")
        if closure.problem_id not in assessment.included_problem_ids:
            missing.append("TEAM_PROBLEM_SCOPE_UNVERIFIED")
        if assessment.uncertainty == "UNKNOWN":
            missing.append("TEAM_BOUNDARY_UNCERTAINTY_UNKNOWN")
        if not (
            closure.dissent_refs
            or assessment.counterevidence_refs
            or assessment.dissent_refs
        ):
            missing.append("MISSING_TEAM_COUNTEREVIDENCE_OR_DISSENT")
    if not closure.validation_evidence:
        missing.append("TEAM_VALIDATION_BASIS_WITHOUT_EVIDENCE")
    if missing:
        raise ValueError(
            f"closure {closure.id!r} claims an active TEAM scope without a valid "
            "BoundaryAssessment gate: " + ", ".join(sorted(set(missing)))
        )


def _evidence_id(owner_type: str, owner_id: str, evidence: EvidenceRef) -> str:
    return f"evidence:{sha256_json({'owner_type': owner_type, 'owner_id': owner_id, 'evidence': evidence.model_dump(mode='json')})[:20]}"


def rebuild_projection(store: Store, *, as_of=None) -> str:
    """Rebuild every derived table from append-only raw events."""
    events = _ordered_events(store, as_of=as_of)
    event_ids = {event.id for event in events}
    problems: dict[str, ProblemThread] = {}
    claims: dict[str, Claim] = {}
    evidence_refs: dict[str, tuple[str, str, str, EvidenceRef]] = {}
    relations: dict[tuple[str, str, str], Relation] = {}
    closures: dict[str, OperationalClosure] = {}
    reopen_signals: dict[str, ReopenSignal] = {}

    for event in events:
        # G0-D: a speculative/generative artifact must not mint derived fact state. Its
        # hints are ignored here, so it can never satisfy TEAM problem coverage.
        speculative = (
            bool(event.metadata.get("speculative"))
            or str(event.metadata.get("event_kind", "")) == "generative_candidate"
        )
        for hint in (() if speculative else event.problem_hints):
            pid = _problem_id(hint)
            generated = ProblemThread(
                id=pid,
                title=hint,
                status=ProblemStatus.HYPOTHESIZED,
                opened_at=event.timestamp,
                scope=_event_scope(event, pid),
                source_event_ids=[event.id],
            )
            problems[pid] = _merge_problem(problems[pid], generated) if pid in problems else generated

        curated = _curated(event)
        for payload in curated.get("problems", []):
            problem = ProblemThread.model_validate(payload)
            if event.id not in problem.source_event_ids:
                problem.source_event_ids.append(event.id)
            problems[problem.id] = (
                _merge_problem(problems[problem.id], problem)
                if problem.id in problems
                else problem
            )

        for payload in curated.get("claims", []):
            claim = Claim.model_validate(payload)
            if event.id not in claim.source_event_ids:
                claim.source_event_ids.append(event.id)
            claims[claim.id] = _merge_claim(claims[claim.id], claim) if claim.id in claims else claim

        for payload in curated.get("evidence_refs", []):
            raw = dict(payload)
            evidence_id = str(raw.pop("id", ""))
            owner_type = str(raw.pop("owner_type", "event"))
            owner_id = str(raw.pop("owner_id", event.id))
            evidence = EvidenceRef.model_validate(raw)
            if evidence.source_event_id not in event_ids:
                raise ValueError(
                    f"EvidenceRef {evidence.source_event_id!r} is not present in raw event history"
                )
            evidence_id = evidence_id or _evidence_id(owner_type, owner_id, evidence)
            evidence_refs[evidence_id] = (evidence_id, owner_type, owner_id, evidence)

        for payload in curated.get("relations", []):
            relation = Relation.model_validate(payload)
            key = (relation.src_id, relation.dst_id, relation.relation_type)
            relations[key] = relation

        for payload in curated.get("closures", []):
            closure = OperationalClosure.model_validate(payload)
            _admit_closure(closures, closure, event_ids)

        for payload in curated.get("reopen_signals", []):
            signal = ReopenSignal.model_validate(payload)
            reopen_signals[signal.id] = signal

        lifecycle = event.metadata.get("closure_event")
        if isinstance(lifecycle, dict):
            closure_payload = lifecycle.get("closure")
            if isinstance(closure_payload, dict):
                closure = OperationalClosure.model_validate(closure_payload)
                _admit_closure(closures, closure, event_ids)
            signal_payload = lifecycle.get("signal")
            if isinstance(signal_payload, dict):
                signal = ReopenSignal.model_validate(signal_payload)
                reopen_signals[signal.id] = signal

    for problem in problems.values():
        problem.source_event_ids = [event_id for event_id in problem.source_event_ids if event_id in event_ids]
    projection_hash = store.replace_projection(
        problems=sorted(problems.values(), key=lambda item: item.id),
        claims=sorted(claims.values(), key=lambda item: item.id),
        evidence_refs=[evidence_refs[key] for key in sorted(evidence_refs)],
        relations=[relations[key] for key in sorted(relations)],
        closures=sorted(closures.values(), key=lambda item: item.id),
        reopen_signals=sorted(reopen_signals.values(), key=lambda item: item.id),
    )
    return projection_hash


def replay_projection(store: Store, *, as_of=None) -> dict[str, Any]:
    """Run the same reducer on an isolated derived store for deterministic replay."""
    events = _ordered_events(store, as_of=as_of)
    replay_store = Store(":memory:")
    replay_store.init_schema()
    replay_store.append_events(events)
    projection_hash = rebuild_projection(replay_store)
    payload = replay_store.projection_payload()
    replay_store.close()
    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "event_count": len(events),
        "projection_hash": projection_hash,
        "replay_hash": sha256_json(payload),
    }


def actor_cursor(store: Store, actor_id: str, *, as_of=None) -> ActorCursor:
    """Return observation/exposure cursors without implying identity or permissions."""
    events = _ordered_events(store, as_of=as_of)
    observed = [
        event
        for event in events
        if event.actor_id == actor_id
        or actor_id in _metadata_actor_ids(event.metadata, "observed_actor_ids")
    ]
    exposed = [
        event
        for event in events
        if actor_id in _metadata_actor_ids(event.metadata, "exposed_actor_ids")
        or actor_id in _metadata_actor_ids(event.metadata, "exposure_actor_ids")
    ]
    return ActorCursor(
        actor_id=actor_id,
        observation_cursor=_cursor_value(observed[-1]) if observed else None,
        exposure_cursor=_cursor_value(exposed[-1]) if exposed else None,
        observed_event_ids=tuple(event.id for event in observed),
        exposed_event_ids=tuple(event.id for event in exposed),
        as_of=as_of,
    )


def load_dissent_records(store: Store, *, as_of=None) -> list[DissentRecord]:
    """Project dissent evidence without collapsing it into a boolean flag."""

    records: list[DissentRecord] = []
    for event in _ordered_events(store, as_of=as_of):
        curated = event.metadata.get("curated", {})
        claims = curated.get("claims", []) if isinstance(curated, dict) else []
        dissent_claims = [
            claim
            for claim in claims
            if isinstance(claim, dict) and str(claim.get("type", "")).upper() == "DISSENT"
        ]
        roles = event.metadata.get("roles", event.metadata.get("tags", []))
        role_set = (
            {str(role).lower() for role in roles}
            if isinstance(roles, (list, tuple, set))
            else {str(roles).lower()}
        )
        if not dissent_claims and not role_set.intersection({"dissent", "veto", "contradiction"}):
            continue
        if not dissent_claims:
            dissent_claims = [{"id": f"dissent:{event.id}"}]
        for claim in dissent_claims:
            claim_id = str(claim.get("id", f"dissent:{event.id}"))
            raw_status = event.metadata.get("dissent_status", DissentStatus.PRESERVED.value)
            try:
                status = DissentStatus(raw_status)
            except ValueError:
                status = DissentStatus.PRESERVED
            basis_kind = EvidenceKind.DISSENT
            records.append(
                DissentRecord(
                    id=f"dissent:{sha256_json({'event_id': event.id, 'claim_id': claim_id})[:20]}",
                    target_claim_id=claim_id,
                    actor_id=event.actor_id,
                    timestamp=event.timestamp,
                    scope=_event_scope(event, event.problem_hints[0] if event.problem_hints else ""),
                    basis=(EvidenceRef(source_event_id=event.id, evidence_kind=basis_kind),),
                    relevance_condition=str(
                        event.metadata.get(
                            "dissent_relevance_condition",
                            "reactivate when the contested proposition returns to an in-scope decision",
                        )
                    ),
                    status=status,
                    later_outcome=(
                        str(event.metadata["dissent_later_outcome"])
                        if event.metadata.get("dissent_later_outcome") is not None
                        else None
                    ),
                    source_event_ids=(event.id,),
                )
            )
    return records


def reactivate_dissent(
    record: DissentRecord,
    *,
    outcome: str,
) -> DissentRecord:
    """Return a new derived dissent state; never mutate the source record."""

    if not outcome.strip():
        raise ValueError("dissent reactivation requires a later outcome")
    return record.model_copy(
        update={
            "status": DissentStatus.REACTIVATED,
            "later_outcome": outcome,
        }
    )


def _metadata_actor_ids(metadata: dict[str, Any], key: str) -> set[str]:
    values = metadata.get(key, [])
    if isinstance(values, str):
        return {values}
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _ordered_events(store: Store, *, as_of=None) -> list[RawEvent]:
    """Return the replay order used by every time-scoped derived operation.

    G0-A: admission follows the unified rule (ADR U1) and ordering is the epistemic
    order (known_at, occurred_at, id, content_hash). With the optional time fields unset
    both reduce to the legacy behaviour, so historical replays are unchanged.
    """

    from .temporal import epistemic_order_key, visible_events

    return sorted(visible_events(store.list_events(), as_of), key=epistemic_order_key)


def _cursor_value(event: RawEvent) -> str:
    return f"{event.timestamp.isoformat()}::{event.id}"


def load_problems(store: Store) -> list[ProblemThread]:
    return [ProblemThread.model_validate(_load_payload(row)) for row in store.list_projection("problems")]


def load_claims(store: Store) -> list[Claim]:
    return [Claim.model_validate(_load_payload(row)) for row in store.list_projection("claims")]


def load_relations(store: Store) -> list[Relation]:
    return [Relation.model_validate(_load_payload(row)) for row in store.list_projection("relations")]


def load_closures(store: Store) -> list[OperationalClosure]:
    return [OperationalClosure.model_validate(_load_payload(row)) for row in store.list_projection("closures")]


def load_reopen_signals(store: Store) -> list[ReopenSignal]:
    return [ReopenSignal.model_validate(_load_payload(row)) for row in store.list_projection("reopen_signals")]


def _load_payload(row: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(row["payload_json"])


def problem_event_index(store: Store) -> dict[str, list[str]]:
    rows = store.connection.execute(
        "SELECT problem_id, event_id FROM problem_event_links ORDER BY problem_id, event_id"
    ).fetchall()
    index: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        index[row["problem_id"]].append(row["event_id"])
    return dict(index)


def distributed_view(store: Store, event_ids: list[str] | None = None) -> dict[str, Any]:
    selected = set(event_ids or [])
    problems = load_problems(store)
    claims = load_claims(store)
    closures = load_closures(store)
    index = problem_event_index(store)
    active_problem_ids = [
        problem.id
        for problem in problems
        if problem.status in {ProblemStatus.ACTIVE, ProblemStatus.REOPENED}
        and (not selected or selected.intersection(index.get(problem.id, [])))
    ]
    relevant_claim_ids = [
        claim.id
        for claim in claims
        if not selected or selected.intersection(claim.source_event_ids)
    ]
    dissent_ids = [
        claim.id
        for claim in claims
        if claim.type == "DISSENT" and (not selected or selected.intersection(claim.source_event_ids))
    ]
    active_closure_ids = [
        closure.id
        for closure in closures
        if closure.status == "ACTIVE" and (not selected or _closure_touches(closure, selected))
    ]
    reopened_closure_ids = [
        closure.id
        for closure in closures
        if closure.status == "REOPENED" and (not selected or _closure_touches(closure, selected))
    ]
    missing_scope = [problem.id for problem in problems if problem.scope.label == "UNSCOPED"]
    return {
        "active_problem_ids": active_problem_ids,
        "relevant_event_ids": sorted(selected),
        "claim_ids": relevant_claim_ids,
        "dissent_ids": dissent_ids,
        "active_closure_ids": active_closure_ids,
        "reopened_closure_ids": reopened_closure_ids,
        "unresolved_ids": [claim.id for claim in claims if claim.status in {"HYPOTHESIZED", "CONTESTED"}],
        "missing_scope": missing_scope,
    }


def _closure_touches(closure: OperationalClosure, event_ids: set[str]) -> bool:
    refs = (
        closure.supporting_evidence
        + closure.dissent_refs
        + closure.unresolved_refs
        + closure.validation_evidence
    )
    return bool(event_ids.intersection(item.source_event_id for item in refs))
