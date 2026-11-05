"""FR-02 regression: derived closure state must not be rewritable by appended raw metadata.

Reported by the fresh conformance review (Finding B) and the fresh adversarial review (A4/A6)
independently: rebuild_projection materialised metadata.curated.closures (and metadata.closure_event)
verbatim with no validation, ordered by event timestamp. One appended raw event dated after a revoke
reinstated a REVOKED closure as ACTIVE, and a curated payload with dangling evidence could mint an
ACTIVE TEAM closure without ever passing evaluate_closure or BoundaryAssessment.

Invariants under test:
  - a REVOKED closure version is terminal (V02R-SPEC revoke semantics);
  - derived closure state carries provenance, so referenced evidence must exist in raw history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from team_soul.config import RuntimeConfig
from team_soul.contracts import ActorType, ClosureStatus, RawEvent
from team_soul.ingest import load_fixture
from team_soul.operators import persist_closure_proposal, revoke_closure, validate_closure
from team_soul.projection import load_closures, rebuild_projection
from team_soul.storage import Store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "C01_historical_revision.json"


def _prepared_store(tmp_path: Path) -> tuple[Store, object]:
    case = load_fixture(FIXTURE)
    store = Store(tmp_path / "closure.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    return store, case


def _revoked_closure(tmp_path: Path):
    store, case = _prepared_store(tmp_path)
    config = RuntimeConfig()
    persist_closure_proposal(case.closure_proposal, store, config)
    validated = validate_closure(case.closure_proposal, store, config)
    revoked = revoke_closure(store, validated.id, "owner revoke")
    assert revoked.status == ClosureStatus.REVOKED
    return store, case, revoked


def _inject(store: Store, case, body: dict, *, event_id: str) -> None:
    latest = max(event.timestamp for event in case.events)
    store.append_event(
        RawEvent(
            id=event_id,
            timestamp=latest + timedelta(days=3650),
            actor_id="attacker",
            actor_type=ActorType.TOOL,
            source_type="injection",
            source_locator="injection/closure",
            raw_content="injected closure payload",
            metadata=body,
        )
    )


def _current(store: Store, closure_id: str):
    return next((item for item in load_closures(store) if item.id == closure_id), None)


def test_curated_closure_cannot_reinstate_a_revoked_closure(tmp_path: Path) -> None:
    store, case, revoked = _revoked_closure(tmp_path)
    reinstated = revoked.model_copy(
        update={"status": ClosureStatus.ACTIVE, "reason_codes": ["CURATED_REINSTATE"]}
    )
    _inject(
        store,
        case,
        {"curated": {"closures": [reinstated.model_dump(mode="json")]}},
        event_id="evt:curated-reinstate",
    )
    rebuild_projection(store)
    current = _current(store, revoked.id)
    assert current is not None and current.status == ClosureStatus.REVOKED


def test_closure_event_cannot_reinstate_a_revoked_closure(tmp_path: Path) -> None:
    store, case, revoked = _revoked_closure(tmp_path)
    reinstated = revoked.model_copy(update={"status": ClosureStatus.ACTIVE})
    _inject(
        store,
        case,
        {"closure_event": {"event_kind": "validated", "closure": reinstated.model_dump(mode="json")}},
        event_id="evt:closure-event-reinstate",
    )
    rebuild_projection(store)
    current = _current(store, revoked.id)
    assert current is not None and current.status == ClosureStatus.REVOKED


def test_curated_closure_with_dangling_evidence_is_rejected(tmp_path: Path) -> None:
    store, case = _prepared_store(tmp_path)
    config = RuntimeConfig()
    persist_closure_proposal(case.closure_proposal, store, config)
    validated = validate_closure(case.closure_proposal, store, config)
    closure = validated.model_copy(
        update={
            "id": "closure:forged-dangling",
            "status": ClosureStatus.ACTIVE,
        }
    )
    payload = closure.model_dump(mode="json")
    payload["supporting_evidence"] = [
        {**dict(payload["supporting_evidence"][0]), "source_event_id": "evt:does-not-exist"}
    ]
    _inject(
        store,
        case,
        {"curated": {"closures": [payload]}},
        event_id="evt:curated-dangling",
    )
    with pytest.raises(ValueError):
        rebuild_projection(store)


def test_gated_closure_lifecycle_still_works(tmp_path: Path) -> None:
    store, case = _prepared_store(tmp_path)
    config = RuntimeConfig()
    proposed = persist_closure_proposal(case.closure_proposal, store, config)
    assert proposed.status == ClosureStatus.PROPOSED
    validated = validate_closure(case.closure_proposal, store, config)
    assert validated.status == ClosureStatus.ACTIVE
    revoked = revoke_closure(store, validated.id, "owner revoke")
    assert revoked.status == ClosureStatus.REVOKED
    assert _current(store, validated.id).status == ClosureStatus.REVOKED
