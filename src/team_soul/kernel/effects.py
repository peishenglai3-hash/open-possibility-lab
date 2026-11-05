from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..contracts import ActorType
from ..hashing import sha256_json
from .contracts import EffectOperation, EffectRecord

if TYPE_CHECKING:
    from ..storage import Store


class EffectLedger:
    """Append-only effect metadata with deterministic history reduction.

    The ledger does not update raw events or mutate projections. An effect is
    represented by a raw history event; consumers can fold those events into a
    derived projection and rebuild the same result from history.
    """

    def __init__(self, store: "Store"):
        self.store = store
        self.store.init_schema()

    def append(
        self,
        effect: EffectRecord,
        *,
        actor_id: str = "team-soul-runtime",
        actor_type: ActorType = ActorType.TOOL,
    ) -> EffectRecord:
        missing = [
            event_id
            for event_id in effect.source_event_ids
            if self.store.get_event(event_id) is None
        ]
        if missing:
            raise ValueError(
                "effect source_event_ids refer to missing raw events: "
                + ", ".join(sorted(missing))
            )
        payload = {
            "event_kind": "policy_effect",
            "effect_event": {"effect": effect.model_dump(mode="json")},
        }
        self.store.append_effect_event(
            payload,
            timestamp=effect.created_at,
            effect_id=effect.effect_id,
            operation=EffectOperation(effect.operation).value,
            actor_id=actor_id,
            actor_type=ActorType(actor_type).value,
        )
        return effect

    def records(
        self,
        *,
        target_projection: str | None = None,
        as_of: datetime | None = None,
    ) -> list[EffectRecord]:
        records: list[EffectRecord] = []
        history = list(self.store.list_effect_events())
        for event in self.store.list_events():
            payload = event.metadata.get("effect_event")
            if isinstance(payload, dict):
                history.append((event.timestamp, {"event_kind": "policy_effect", "effect_event": payload}))
        for timestamp, event_payload in sorted(
            history,
            key=lambda item: (item[0], sha256_json(item[1])),
        ):
            if as_of is not None and timestamp > as_of:
                continue
            payload = event_payload.get("effect_event")
            if not isinstance(payload, dict):
                continue
            raw_effect = payload.get("effect")
            if not isinstance(raw_effect, dict):
                continue
            effect = EffectRecord.model_validate(raw_effect)
            if target_projection is None or effect.target_projection == target_projection:
                records.append(effect)
        return records

    def active(
        self,
        *,
        target_projection: str | None = None,
        as_of: datetime | None = None,
    ) -> list[EffectRecord]:
        active: dict[str, EffectRecord] = {}
        for effect in self.records(target_projection=target_projection, as_of=as_of):
            if effect.operation in {EffectOperation.ADD, EffectOperation.SUPERSEDE}:
                if effect.operation == EffectOperation.SUPERSEDE and effect.supersedes_effect_id:
                    active.pop(effect.supersedes_effect_id, None)
                active[effect.effect_id] = effect
            elif effect.operation == EffectOperation.DEACTIVATE:
                target = effect.supersedes_effect_id or str(
                    effect.inverse_spec.get("reverts_effect_id", "")
                )
                if target:
                    active.pop(target, None)
            elif effect.operation == EffectOperation.ANNOTATE:
                continue
        return [active[key] for key in sorted(active)]

    def deactivate(
        self,
        effect_id: str,
        *,
        context_hash: str,
        reason: str,
        source_event_ids: tuple[str, ...] | None = None,
    ) -> EffectRecord:
        previous = next((item for item in self.records() if item.effect_id == effect_id), None)
        if previous is None:
            raise KeyError(f"effect not found: {effect_id}")
        inverse = {
            "reverts_effect_id": effect_id,
            "reason": reason,
            **previous.inverse_spec,
        }
        compensation = EffectRecord(
            effect_id=(
                f"{effect_id}:deactivate:"
                f"{sha256_json({'context_hash': context_hash, 'reason': reason})[:16]}"
            ),
            policy_id=previous.policy_id,
            policy_version=previous.policy_version,
            context_hash=context_hash,
            source_event_ids=source_event_ids or previous.source_event_ids,
            target_projection=previous.target_projection,
            operation=EffectOperation.DEACTIVATE,
            before_hash=previous.after_hash,
            after_hash=previous.before_hash,
            inverse_spec=inverse,
            created_at=datetime.now(timezone.utc),
            supersedes_effect_id=effect_id,
        )
        return self.append(compensation)

    def restore(
        self,
        effect_id: str,
        *,
        context_hash: str,
        reason: str,
        source_event_ids: tuple[str, ...] | None = None,
    ) -> EffectRecord:
        previous = next((item for item in self.records() if item.effect_id == effect_id), None)
        if previous is None:
            raise KeyError(f"effect not found: {effect_id}")
        if any(item.effect_id == effect_id for item in self.active()):
            raise ValueError(f"effect is already active: {effect_id}")
        restored = EffectRecord(
            effect_id=(
                f"{effect_id}:restore:"
                f"{sha256_json({'context_hash': context_hash, 'reason': reason})[:16]}"
            ),
            policy_id=previous.policy_id,
            policy_version=previous.policy_version,
            context_hash=context_hash,
            source_event_ids=source_event_ids or previous.source_event_ids,
            target_projection=previous.target_projection,
            operation=EffectOperation.ADD,
            before_hash=previous.before_hash,
            after_hash=previous.after_hash,
            inverse_spec={
                "restores_effect_id": effect_id,
                "reason": reason,
                **previous.inverse_spec,
            },
            created_at=datetime.now(timezone.utc),
        )
        return self.append(restored)

    def state_hash(
        self,
        *,
        target_projection: str | None = None,
        as_of: datetime | None = None,
    ) -> str:
        return sha256_json(
            [
                effect.model_dump(mode="json")
                for effect in self.active(
                    target_projection=target_projection,
                    as_of=as_of,
                )
            ]
        )
