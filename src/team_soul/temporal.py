"""G0-A unified effective time: the single read interface for the cognitive history.

ADR ``docs/V03-GATE0-ADR-EFFECTIVE-TIME.md`` (owner ruling U1):

    event_time     = occurred_at  if explicitly present else legacy timestamp
    knowledge_time = known_at     if explicitly present else legacy timestamp
    visible(T)     = event_time <= T AND knowledge_time <= T

The RawStore keeps the full record history (ledger/audit order: legacy timestamp + id);
a ``TemporalView`` derives the cognitive history at one frozen ``effective_time``
(epistemic order: known_at, occurred_at, id, content_hash). The two orders must not be
mixed (ADR E1).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterable

from .contracts import RawEvent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .storage import Store


def is_visible(event: RawEvent, effective_time: datetime) -> bool:
    """Owner ruling U1: both clocks must be at or before the decision time."""

    return event.event_time <= effective_time and event.knowledge_time <= effective_time


def visible_events(
    events: Iterable[RawEvent], effective_time: datetime | None
) -> list[RawEvent]:
    """Filter an event stream to the cognitive history at ``effective_time``.

    ``None`` means an unscoped view (legacy callers), which returns the record history.
    """

    if effective_time is None:
        return list(events)
    return [event for event in events if is_visible(event, effective_time)]


def epistemic_order_key(event: RawEvent) -> tuple[datetime, datetime, str, str]:
    """Epistemic/cognitive order (ADR E1): known_at, occurred_at, stable id."""

    return (event.knowledge_time, event.event_time, event.id, event.content_hash)


class TemporalView:
    """The cognitive history of one store at one frozen effective time."""

    def __init__(self, store: "Store", effective_time: datetime | None = None) -> None:
        self.store = store
        self.effective_time = effective_time

    def events(self) -> list[RawEvent]:
        """Admitted events (ADR U1)."""

        return visible_events(self.store.list_events(), self.effective_time)

    def ordered_events(self) -> list[RawEvent]:
        """Admitted events in epistemic order (ADR E1)."""

        return sorted(self.events(), key=epistemic_order_key)


__all__ = ["TemporalView", "epistemic_order_key", "is_visible", "visible_events"]
