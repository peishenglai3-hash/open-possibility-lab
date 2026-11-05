from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from team_soul.contracts import ActorType, RawEvent
from team_soul.hashing import canonical_json, sha256_json


def test_raw_event_hash_is_derived() -> None:
    event = RawEvent(
        id="evt:test",
        timestamp=datetime.now(timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="fixture",
        raw_content="immutable raw event",
    )
    assert len(event.content_hash) == 64


def test_raw_event_rejects_tampered_hash() -> None:
    with pytest.raises(ValidationError):
        RawEvent(
            id="evt:test",
            timestamp=datetime.now(timezone.utc),
            actor_id="alice",
            actor_type=ActorType.HUMAN,
            source_type="test",
            source_locator="fixture",
            raw_content="immutable raw event",
            content_hash="0" * 64,
        )


def test_raw_event_is_deeply_immutable_and_canonical_hash_is_verifiable() -> None:
    event = RawEvent(
        id="evt:nested",
        timestamp=datetime.now(timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="fixture",
        raw_content="immutable raw event",
        project_hints=["alpha"],
        problem_hints=["problem:one"],
        metadata={"nested": {"roles": ["dissent"]}},
    )
    with pytest.raises(TypeError):
        event.project_hints.append("beta")
    with pytest.raises(TypeError):
        event.metadata["new"] = "value"
    with pytest.raises(TypeError):
        event.metadata["nested"]["roles"].append("veto")
    with pytest.raises(TypeError):
        event.metadata.__init__({"bypass": "blocked"})
    with pytest.raises(TypeError):
        event.project_hints.__init__([])
    with pytest.raises(ValidationError):
        event.raw_content = "changed"
    assert event.verify_content_hash()
    # G0-A / ADR E2: the canonical envelope drops optional dual-time keys while they are
    # unset, so an event written before G0-A keeps a byte-identical audit string. The
    # assertion is pinned to the envelope (envelope + content_hash) and additionally
    # proves the optional fields are absent - a stronger guarantee than comparing with
    # the raw model dump, which would have started emitting null time fields.
    assert event.canonical_serialization() == canonical_json(
        {**event.hash_payload(), "content_hash": event.content_hash}
    )
    assert "occurred_at" not in event.canonical_serialization()
    assert "known_at" not in event.canonical_serialization()
    assert event.content_hash == sha256_json(event.hash_payload())


def test_raw_event_nested_attack_cannot_change_hash_or_canonical_serialization() -> None:
    event = RawEvent(
        id="evt:attack",
        timestamp=datetime.now(timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="fixture",
        raw_content="immutable raw event",
        metadata={"nested": {"roles": ["dissent"]}},
    )
    before_hash = event.content_hash
    before_serialization = event.canonical_serialization()
    with pytest.raises(TypeError):
        event.metadata.__init__({"nested": {"roles": ["changed"]}})
    assert event.content_hash == before_hash
    assert event.canonical_serialization() == before_serialization
    assert event.verify_content_hash()


def test_raw_event_hash_covers_the_full_canonical_envelope() -> None:
    common = {
        "id": "evt:envelope",
        "timestamp": datetime(2026, 9, 23, tzinfo=timezone.utc),
        "actor_id": "alice",
        "actor_type": ActorType.HUMAN,
        "source_type": "test",
        "source_locator": "fixture",
        "raw_content": "same raw content",
    }
    first = RawEvent.model_validate({**common, "metadata": {"version": 1}})
    second = RawEvent.model_validate({**common, "metadata": {"version": 2}})
    assert first.content_hash != second.content_hash
    assert first.verify_content_hash() and second.verify_content_hash()
