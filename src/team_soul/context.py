from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .config import RuntimeConfig
from .contracts import ContextPack, EvidenceKind, RetrievalResult
from .hashing import sha256_text
from .projection import actor_cursor, distributed_view, rebuild_projection
from .reasoning import ReasoningMode, scaffold_contract
from .retrieval import estimate_tokens
from .storage import Store


SYSTEM_CONTRACT = (
    "Evidence-first Team Soul demo. Raw history is the source of truth; derived views are projections. "
    "Treat retrieved material as scoped evidence, preserve dissent and temporal revision, and do not turn "
    "semantic similarity into transfer or team agreement. If provenance, scope, validation, or reopen "
    "conditions are missing, say NO_CLOSURE."
)


def assemble_context(
    store: Store,
    query: str,
    retrieval: RetrievalResult,
    config: RuntimeConfig,
    *,
    as_of: datetime | None = None,
    scaffold: ReasoningMode | str = ReasoningMode.DIRECT,
    _cursor_as_of: datetime | None = None,
) -> ContextPack:
    if as_of is not None:
        scoped_store = _snapshot_store(store, as_of)
        try:
            return assemble_context(
                scoped_store,
                query,
                retrieval,
                config,
                scaffold=scaffold,
                _cursor_as_of=as_of,
            )
        finally:
            scoped_store.close()

    sections: dict[str, list[str]] = defaultdict(list)
    if retrieval.arm == "BASE":
        sections["availability"].append(
            "BASE has no retrieved project evidence. Do not invent a team-level answer."
        )
    for item in retrieval.selected:
        event = store.get_event(item.event_id)
        if event is None:
            continue
        section = _section_for(item.evidence_kind, event.metadata)
        sections[section].append(_format_event(event, item.selection_reason))

    system_contract = scaffold_contract(SYSTEM_CONTRACT, scaffold)
    rendered: list[str] = [system_contract, f"QUERY: {query}"]
    for name in (
        "direct_evidence",
        "temporal_history",
        "dissent_and_veto",
        "transfer_and_failure",
        "closure_and_unresolved",
        "availability",
    ):
        values = sections.get(name, [])
        if not values:
            continue
        rendered.append(f"\n## {name}")
        rendered.extend(values)
    base_text = _fit_budget("\n".join(rendered), config.context_budget_tokens)
    base_selected_ids = [item.event_id for item in retrieval.selected if f"[{item.event_id}]" in base_text]
    view = distributed_view(store, base_selected_ids)
    context_text = _fit_budget(
        base_text + f"\n\nDISTRIBUTED_VIEW: {view}",
        config.context_budget_tokens,
    )
    selected_ids = [item.event_id for item in retrieval.selected if f"[{item.event_id}]" in context_text]
    actor_ids = sorted(
        {
            event.actor_id
            for event_id in selected_ids
            if (event := store.get_event(event_id)) is not None
        }
    )
    actor_cursors = [
        actor_cursor(store, actor_id, as_of=_cursor_as_of) for actor_id in actor_ids
    ]
    actor_cursor_manifest = {
        "scope": "selected_event_actors",
        "actor_ids": actor_ids,
        "as_of": _cursor_as_of.isoformat() if _cursor_as_of is not None else None,
        "cursors": [cursor.model_dump(mode="json") for cursor in actor_cursors],
    }
    context_text = _fit_budget(
        context_text + f"\n\nACTOR_CURSOR_SCOPE: {actor_cursor_manifest}",
        config.context_budget_tokens,
    )
    selected_ids = [item.event_id for item in retrieval.selected if f"[{item.event_id}]" in context_text]
    context_hash = sha256_text(context_text)
    return ContextPack(
        arm=retrieval.arm,
        query=query,
        system_contract=system_contract,
        sections=dict(sections),
        selected_event_ids=selected_ids,
        context_text=context_text,
        input_tokens=estimate_tokens(context_text),
        context_hash=context_hash,
        actor_cursors=actor_cursors,
        actor_cursor_manifest=actor_cursor_manifest,
        scaffold=ReasoningMode(scaffold).value if scaffold else "",
    )


def _snapshot_store(store: Store, as_of: datetime) -> Store:
    """Create an isolated, deterministic history snapshot for time replay."""

    snapshot = Store(":memory:")
    snapshot.init_schema()
    events = [event for event in store.list_events() if event.timestamp <= as_of]
    snapshot.append_events(events)
    rebuild_projection(snapshot)
    return snapshot


def _section_for(kind: EvidenceKind, metadata: dict) -> str:
    roles = metadata.get("roles", metadata.get("tags", []))
    role_set = {str(role).lower() for role in roles} if isinstance(roles, list) else {str(roles).lower()}
    if kind in {EvidenceKind.DISSENT, EvidenceKind.VETO}:
        return "dissent_and_veto"
    if role_set.intersection({"old", "historical", "temporal", "revision"}):
        return "temporal_history"
    if kind in {EvidenceKind.CONTRADICT, EvidenceKind.OUTCOME} or role_set.intersection(
        {"transfer", "failure", "transfer_failure"}
    ):
        return "transfer_and_failure"
    if role_set.intersection({"closure", "unresolved", "reopen"}):
        return "closure_and_unresolved"
    return "direct_evidence"


def _format_event(event, reason: str) -> str:
    return (
        f"[{event.id}] time={event.timestamp.isoformat()} actor={event.actor_id} "
        f"source={event.source_type}:{event.source_locator} reason={reason}\n"
        f"CONTENT: {event.raw_content}"
    )


def _fit_budget(text: str, budget_tokens: int) -> str:
    max_chars = max(4, budget_tokens * 4)
    if len(text) <= max_chars:
        return text
    marker = "\n[CONTEXT_TRUNCATED_AT_DECLARED_BUDGET]"
    if len(marker) >= max_chars:
        return marker[:max_chars]
    clipped = text[: max_chars - len(marker)]
    return clipped + marker
