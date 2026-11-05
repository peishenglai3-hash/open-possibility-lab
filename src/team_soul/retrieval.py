from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .config import RuntimeConfig
from .contracts import EvidenceKind, RetrievalItem, RetrievalResult, RawEvent
from .projection import load_closures, load_claims, problem_event_index
from .storage import Store


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


@dataclass(frozen=True)
class Candidate:
    event: RawEvent
    score: float
    channel: str
    reason: str


def retrieve(
    store: Store,
    query: str,
    arm: str,
    config: RuntimeConfig,
    *,
    as_of: datetime | None = None,
) -> RetrievalResult:
    if as_of is not None:
        from .temporal import visible_events

        scoped_store = Store(":memory:")
        scoped_store.init_schema()
        scoped_store.append_events(visible_events(store.list_events(), as_of))
        from .projection import rebuild_projection

        rebuild_projection(scoped_store)
        try:
            return retrieve(scoped_store, query, arm, config)
        finally:
            scoped_store.close()

    normalized_arm = arm.upper()
    if normalized_arm == "BASE":
        return RetrievalResult(
            arm="BASE",
            query=query,
            candidate_pool_ids=[],
            selected=[],
            context_tokens=0,
            budget_tokens=config.context_budget_tokens,
        )

    direct = store.search_events(query, limit=config.candidate_top_n)
    candidates: dict[str, Candidate] = {
        event.id: Candidate(
            event=event,
            score=1.0 / (config.rrf_k + rank),
            channel="lexical",
            reason="direct query match",
        )
        for rank, (event, _score) in enumerate(direct, start=1)
    }
    if normalized_arm == "SOUL":
        _expand_distributed_candidates(store, direct, candidates)
    elif normalized_arm not in {"RAG", "LONG_CONTEXT"}:
        raise ValueError(f"Unsupported retrieval arm: {arm}")

    ordered = sorted(
        candidates.values(),
        key=lambda item: (-item.score, item.event.timestamp, item.event.id),
    )
    selected = _select(
        ordered,
        arm=normalized_arm,
        budget_tokens=config.context_budget_tokens,
    )
    selected_tokens = sum(item.estimated_tokens for item in selected)
    return RetrievalResult(
        arm=normalized_arm,
        query=query,
        candidate_pool_ids=[item.event.id for item in ordered],
        selected=selected,
        context_tokens=selected_tokens,
        budget_tokens=config.context_budget_tokens,
    )


def _expand_distributed_candidates(
    store: Store,
    direct: list[tuple[RawEvent, float]],
    candidates: dict[str, Candidate],
) -> None:
    event_to_problems: dict[str, set[str]] = defaultdict(set)
    problem_events = problem_event_index(store)
    for problem_id, event_ids in problem_events.items():
        for event_id in event_ids:
            event_to_problems[event_id].add(problem_id)

    claims = load_claims(store)
    closures = load_closures(store)
    for event, direct_score in direct:
        problem_ids = event_to_problems.get(event.id, set())
        expanded_ids: set[str] = set()
        for problem_id in problem_ids:
            expanded_ids.update(problem_events.get(problem_id, []))
        for claim in claims:
            if problem_ids.intersection(claim.problem_ids) or event.id in claim.source_event_ids:
                expanded_ids.update(claim.source_event_ids)
        for closure in closures:
            refs = (
                closure.supporting_evidence
                + closure.dissent_refs
                + closure.unresolved_refs
                + closure.validation_evidence
            )
            ref_ids = {ref.source_event_id for ref in refs}
            if event.id in ref_ids or problem_ids.intersection({closure.problem_id}):
                expanded_ids.update(ref_ids)
        for expanded_id in sorted(expanded_ids):
            expanded = store.get_event(expanded_id)
            if expanded is None or expanded.id == event.id:
                continue
            expanded_score = max(candidates.get(expanded.id, Candidate(expanded, 0.0, "", "")).score, direct_score * 0.65)
            existing = candidates.get(expanded.id)
            reason = "problem-linked expansion"
            if _kind(expanded) in {EvidenceKind.DISSENT, EvidenceKind.VETO}:
                reason = "protected dissent/veto expansion"
            if existing is None or expanded_score > existing.score:
                candidates[expanded.id] = Candidate(
                    event=expanded,
                    score=expanded_score,
                    channel="soul-expansion",
                    reason=reason,
                )


def _select(ordered: list[Candidate], *, arm: str, budget_tokens: int) -> list[RetrievalItem]:
    if arm == "LONG_CONTEXT":
        arm = "RAG"
    if arm == "RAG":
        selected: list[RetrievalItem] = []
        used = 0
        for candidate in ordered:
            item = _item(candidate)
            if used + item.estimated_tokens > budget_tokens:
                continue
            selected.append(item)
            used += item.estimated_tokens
        return selected

    # SOUL protected-slot policy from the engineering plan. Slots are token
    # budgets; unused protected capacity is deliberately returned to the
    # remaining candidate pool rather than filled with fabricated evidence.
    slot_ratio = {
        "direct": 0.40,
        "temporal": 0.20,
        "dissent": 0.15,
        "transfer_failure": 0.15,
        "closure_unresolved": 0.10,
    }
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in ordered:
        grouped[_slot(candidate.event)].append(candidate)
    selected: list[RetrievalItem] = []
    selected_ids: set[str] = set()
    used = 0
    for slot, ratio in slot_ratio.items():
        slot_budget = max(1, math.floor(budget_tokens * ratio))
        slot_used = 0
        for candidate in grouped.get(slot, []):
            if candidate.event.id in selected_ids:
                continue
            item = _item(candidate)
            if slot_used + item.estimated_tokens > slot_budget and slot_used > 0:
                continue
            if used + item.estimated_tokens > budget_tokens:
                continue
            selected.append(item)
            selected_ids.add(candidate.event.id)
            used += item.estimated_tokens
            slot_used += item.estimated_tokens

    for candidate in ordered:
        if candidate.event.id in selected_ids:
            continue
        item = _item(candidate)
        if used + item.estimated_tokens > budget_tokens:
            continue
        selected.append(item)
        selected_ids.add(candidate.event.id)
        used += item.estimated_tokens
    return selected


def _item(candidate: Candidate) -> RetrievalItem:
    return RetrievalItem(
        event_id=candidate.event.id,
        score=round(candidate.score, 8),
        channel=candidate.channel,
        evidence_kind=_kind(candidate.event),
        selection_reason=candidate.reason,
        estimated_tokens=estimate_tokens(candidate.event.raw_content),
    )


def _kind(event: RawEvent) -> EvidenceKind:
    explicit = event.metadata.get("evidence_kind")
    if explicit:
        try:
            return EvidenceKind(explicit)
        except ValueError:
            pass
    roles = event.metadata.get("roles", event.metadata.get("tags", []))
    roles = {str(role).lower() for role in roles} if isinstance(roles, list) else {str(roles).lower()}
    if roles.intersection({"veto", "hard_veto"}):
        return EvidenceKind.VETO
    if roles.intersection({"dissent", "contradiction"}):
        return EvidenceKind.DISSENT
    if roles.intersection({"failure", "outcome"}):
        return EvidenceKind.OUTCOME
    if roles.intersection({"transfer", "transfer_failure"}):
        return EvidenceKind.CONTRADICT
    if roles.intersection({"support", "validation"}):
        return EvidenceKind.SUPPORT
    return EvidenceKind.CONTEXT


def _slot(event: RawEvent) -> str:
    roles = event.metadata.get("roles", event.metadata.get("tags", []))
    role_set = {str(role).lower() for role in roles} if isinstance(roles, list) else {str(roles).lower()}
    if role_set.intersection({"dissent", "veto", "hard_veto", "contradiction"}):
        return "dissent"
    if role_set.intersection({"transfer", "failure", "transfer_failure", "outcome"}):
        return "transfer_failure"
    if role_set.intersection({"closure", "unresolved", "reopen"}):
        return "closure_unresolved"
    if role_set.intersection({"old", "historical", "temporal", "revision"}):
        return "temporal"
    return "direct"
