"""G0-E: actor exposure semantics (ActorView vs TeamView).

ADR G0-E: ActorCursor expresses observation/exposure, never IAM. A TEAM-level judgement
may integrate different actors and sources, but it must keep actor_coverage, unseen_to and
contested_by visible, because TEAM does not mean that everybody knows.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .contracts import RawEvent
from .projection import actor_cursor
from .temporal import visible_events

if TYPE_CHECKING:  # pragma: no cover
    from .storage import Store


class ActorView:
    """What one actor could see at one effective time (exposure, not authorization)."""

    def __init__(self, store: "Store", actor_id: str, effective_time: datetime | None = None) -> None:
        self.store = store
        self.actor_id = actor_id
        self.effective_time = effective_time

    def event_ids(self) -> set[str]:
        cursor = actor_cursor(self.store, self.actor_id, as_of=self.effective_time)
        exposed = set(cursor.observed_event_ids) | set(cursor.exposed_event_ids)
        return {event.id for event in self.events()} & exposed

    def events(self) -> list[RawEvent]:
        return visible_events(self.store.list_events(), self.effective_time)


class TeamView:
    """The union of actor views at one effective time, with coverage kept explicit."""

    def __init__(self, store: "Store", effective_time: datetime | None = None) -> None:
        self.store = store
        self.effective_time = effective_time

    def events(self) -> list[RawEvent]:
        return visible_events(self.store.list_events(), self.effective_time)

    def coverage(self) -> dict[str, Any]:
        events = self.events()
        actors = sorted({event.actor_id for event in events})
        seen: dict[str, set[str]] = {}
        for actor in actors:
            seen[actor] = ActorView(self.store, actor, self.effective_time).event_ids()
        unseen_to = {
            actor: sorted({event.id for event in events} - seen[actor]) for actor in actors
        }
        contested_by = sorted(
            {
                event.actor_id
                for event in events
                if {"dissent", "veto", "contradiction"}
                & {str(role).lower() for role in event.metadata.get("roles", []) or []}
            }
        )
        covered = {event.id for event in events if event.id in set().union(*seen.values())} if seen else set()
        return {
            "actor_ids": actors,
            "actor_coverage": (len(covered) / len(events)) if events else 0.0,
            "unseen_to": unseen_to,
            "contested_by": contested_by,
        }


__all__ = ["ActorView", "TeamView"]
