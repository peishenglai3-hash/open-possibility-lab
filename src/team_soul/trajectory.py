"""MC-R1: Situated Judgement / Interpretive Trajectory - a DERIVED, read-only operator.

Frozen contract: docs/V03-MC-R1.md. Implementation envelope (directive section 7.2): one derived
data structure, one near-pure derive function, one validator group. Nothing is persisted, no new
truth layer is created, and RawEvent is never modified.

Three rules are structural rather than aspirational:
  * every node binds the source event ids it was derived from - an element with no binding is
    reported as PARTIAL, never filled in;
  * nodes are ordered by their own source timestamps, so a later conclusion cannot be attached to
    an earlier moment (no backwriting) - this is enforced by construction, not by instruction;
  * a role proposal may only REFINE a role. It can never introduce an event, move time, or change
    scope; a proposal without provenance is rejected. (Directive section 7.3.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from .contracts import RawEvent
from .temporal import is_visible

# The frozen transformation from MC-R1 section 1, in order.
TRAJECTORY_ROLES = (
    "SITUATION",
    "INITIAL_FRAMING",
    "COMPETING_INTERPRETATION",
    "TURNING_POINT",
    "DECISION_OR_CLOSURE",
    "OUTCOME",
    "REVISION",
    "UNRESOLVED",
)

# Deterministic role vocabulary read from the record, never invented.
_FRAMING_ROLES = {"failure", "finding"}
_DISSENT_ROLES = {"dissent", "contradiction"}
_DECISION_ROLES = {"validation"}
_OUTCOME_ROLES = {"outcome"}
_REVISION_ROLES = {"revision", "correction"}
_UNRESOLVED_ROLES = {"unresolved"}

# Directive 7.3: a proposal may refine, never decide provenance, ordering or scope. So it is
# admissible only for roles that are NOT read from the record's own role vocabulary, and only to
# FILL A GAP the deterministic pass left empty. It can never reassign a derived role.
REFINABLE_ROLES = ("OUTCOME", "UNRESOLVED")


def _roles(event: RawEvent) -> set[str]:
    metadata = event.metadata or {}
    raw = metadata.get("roles", metadata.get("tags", []))
    if isinstance(raw, str):
        return {raw.lower()}
    return {str(role).lower() for role in (raw or [])}


@dataclass(frozen=True)
class TrajectoryNode:
    """One element of the trajectory. `event_ids` is the provenance, and it is mandatory."""

    role: str
    event_ids: tuple[str, ...]
    timestamp: datetime
    basis: str
    provenance_complete: bool = True


@dataclass(frozen=True)
class InterpretiveTrajectory:
    """A DERIVED artifact. It is not a record of what happened; it is a reading of the record."""

    case_id: str
    effective_time: datetime | None
    nodes: tuple[TrajectoryNode, ...] = ()
    status: str = "UNKNOWN"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def roles(self) -> tuple[str, ...]:
        return tuple(node.role for node in self.nodes)

    def as_lines(self) -> list[str]:
        lines: list[str] = []
        for node in self.nodes:
            marker = "" if node.provenance_complete else " [PARTIAL]"
            lines.append(
                f"{node.role}{marker} ({node.timestamp.isoformat()}) "
                f"sources={list(node.event_ids)} basis={node.basis}"
            )
        return lines


def _selector(role: str, events: Sequence[RawEvent]) -> tuple[str, list[RawEvent], str] | None:
    """Pick the events that carry a role, deterministically, from the record alone."""

    ordered = sorted(events, key=lambda event: (event.timestamp, event.id))
    if role == "SITUATION":
        return (role, ordered[:1], "earliest visible record of the problem") if ordered else None
    if role == "INITIAL_FRAMING":
        picks = [event for event in ordered if _roles(event) & _FRAMING_ROLES]
        return (role, picks[:1], "first record carrying a finding/failure role") if picks else None
    if role == "COMPETING_INTERPRETATION":
        picks = [event for event in ordered if _roles(event) & _DISSENT_ROLES]
        return (role, picks, "records carrying dissent/contradiction roles") if picks else None
    if role == "DECISION_OR_CLOSURE":
        picks = [
            event
            for event in ordered
            if _roles(event) & _DECISION_ROLES and not (_roles(event) & _DISSENT_ROLES)
        ]
        return (role, picks[:1], "recorded validation/closure event") if picks else None
    if role == "OUTCOME":
        picks = [event for event in ordered if _roles(event) & _OUTCOME_ROLES]
        # No cap: `[:3]` silently dropped the post-dissent decision in C76, so the reading ended
        # before the reopening it exists to explain. Found by the adversarial reviewer, epoch 4.
        return (role, picks, "records carrying an outcome role") if picks else None
    if role == "REVISION":
        picks = [event for event in ordered if _roles(event) & _REVISION_ROLES]
        return (role, picks, "records carrying a revision/correction role") if picks else None
    if role == "UNRESOLVED":
        picks = [event for event in ordered if _roles(event) & _UNRESOLVED_ROLES]
        return (role, picks, "records still marked unresolved") if picks else None
    return None


def derive_interpretive_trajectory(
    case_id: str,
    events: Iterable[RawEvent],
    *,
    effective_time: datetime | None = None,
    role_proposals: Mapping[str, str] | None = None,
) -> InterpretiveTrajectory:
    """Derive the trajectory. Near-pure: no I/O, no clock, no provider call.

    `role_proposals` maps an event id to a PROPOSED role (directive section 7.3: an LLM may propose
    candidate roles but may not decide provenance, ordering or scope). A proposal is accepted only
    if the event exists, is visible at `effective_time`, and the proposed role is in the frozen
    vocabulary; otherwise it is rejected and the rejection is reported in the notes.
    """

    visible = [event for event in events if effective_time is None or is_visible(event, effective_time)]
    notes: list[str] = []
    accepted: dict[str, str] = {}

    for event_id, proposed in (role_proposals or {}).items():
        if proposed not in TRAJECTORY_ROLES:
            notes.append(f"proposal rejected for {event_id}: role {proposed!r} is not in the frozen vocabulary")
            continue
        match = next((event for event in visible if event.id == event_id), None)
        if match is None:
            notes.append(f"proposal rejected for {event_id}: no such visible event (provenance missing)")
            continue
        if proposed not in REFINABLE_ROLES:
            notes.append(
                f"proposal rejected for {event_id}: {proposed} is derived from the record and may not be reassigned"
            )
            continue
        accepted[event_id] = proposed

    nodes: list[TrajectoryNode] = []
    for role in TRAJECTORY_ROLES:
        if role == "TURNING_POINT":
            continue  # derived below, because it depends on the decision
        chosen = _selector(role, visible)
        if chosen is None:
            continue
        _, picks, basis = chosen
        # A proposal may never remove or replace a role the record itself carries. Suppressing
        # picks through `accepted` here was a defect: a proposal that was explicitly NOT applied
        # still deleted nodes (found by the adversarial reviewer, epoch 4).
        nodes.append(
            TrajectoryNode(
                role=role,
                event_ids=tuple(event.id for event in picks),
                timestamp=min(event.timestamp for event in picks),
                basis=basis,
            )
        )

    # The turning point is the earliest dissent that post-dates the recorded decision - i.e. the
    # event that actually revised the reading. It is derived, not asserted.
    decision = next((node for node in nodes if node.role == "DECISION_OR_CLOSURE"), None)
    if decision is not None:
        later = [
            event
            for event in visible
            if _roles(event) & _DISSENT_ROLES and event.timestamp > decision.timestamp
        ]
        if later:
            earliest = min(event.timestamp for event in later)
            picks = [event for event in later if event.timestamp == earliest]
            nodes.append(
                TrajectoryNode(
                    role="TURNING_POINT",
                    event_ids=tuple(event.id for event in picks),
                    timestamp=earliest,
                    basis="earliest dissent post-dating the recorded decision",
                )
            )

    # An accepted proposal may fill a gap the record left - never overwrite what it stated.
    present = {node.role for node in nodes}
    for event_id, proposed in accepted.items():
        if proposed in present:
            notes.append(f"proposal for {event_id} not applied: a {proposed} node already exists from the record")
            continue
        event = next(event for event in visible if event.id == event_id)
        nodes.append(
            TrajectoryNode(
                role=proposed,
                event_ids=(event_id,),
                timestamp=event.timestamp,
                basis="proposed by an external proposer; event exists and is visible at effective_time",
            )
        )
        present.add(proposed)

    nodes.sort(key=lambda node: (node.timestamp, node.role))
    if not nodes:
        return InterpretiveTrajectory(
            case_id=case_id,
            effective_time=effective_time,
            nodes=(),
            status="UNKNOWN",
            notes=tuple(notes + ["no role could be derived from the visible record"]),
        )
    missing = [role for role in ("INITIAL_FRAMING", "TURNING_POINT", "REVISION") if role not in {n.role for n in nodes}]
    status = "COMPLETE" if not missing else "PARTIAL"
    if missing:
        notes.append("no event carries: " + ", ".join(missing))
    return InterpretiveTrajectory(
        case_id=case_id,
        effective_time=effective_time,
        nodes=tuple(nodes),
        status=status,
        notes=tuple(notes),
    )


def validate_trajectory(
    trajectory: InterpretiveTrajectory,
    events: Sequence[RawEvent],
    *,
    effective_time: datetime | None = None,
) -> tuple[bool, list[str]]:
    """Provenance and ordering checks. Returns (ok, problems); never repairs silently."""

    problems: list[str] = []
    by_id = {event.id: event for event in events}
    seen_timestamps: list[datetime] = []
    for node in trajectory.nodes:
        if node.role not in TRAJECTORY_ROLES:
            problems.append(f"unknown role {node.role!r}")
        if not node.event_ids:
            problems.append(f"{node.role}: no source event bound (must be PARTIAL, not asserted)")
            continue
        for event_id in node.event_ids:
            event = by_id.get(event_id)
            if event is None:
                problems.append(f"{node.role}: source {event_id} does not exist in the corpus")
                continue
            if effective_time is not None and not is_visible(event, effective_time):
                problems.append(f"{node.role}: source {event_id} is not visible at effective_time")
            if event.timestamp < node.timestamp:
                problems.append(f"{node.role}: source {event_id} predates its own node")
        seen_timestamps.append(node.timestamp)
    if seen_timestamps != sorted(seen_timestamps):
        problems.append("nodes are not ordered by source time (backwriting risk)")
    return (not problems), problems
