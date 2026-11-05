"""Pre-W02 ADV-A6 regression: curated ACTIVE TEAM closures must pass the boundary gate.

The fresh adversarial review (A6) showed that metadata.curated.closures was
materialised verbatim: an ACTIVE TEAM closure whose evidence happened to exist was
admitted without ever calling evaluate_closure / _team_boundary_reasons.

Invariant under test: an active TEAM closure can only appear in the projection when
it carries the boundary evidence the gate requires. Non-TEAM and non-active closures
are unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from team_soul.contracts import (
    ActorType,
    BoundaryAssessment,
    ClosureStatus,
    EvidenceKind,
    EvidenceRef,
    OperationalClosure,
    RawEvent,
    Scope,
)
from team_soul.projection import load_closures, rebuild_projection
from team_soul.storage import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "team-closure.sqlite3")
    store.init_schema()
    store.append_events(
        [
            RawEvent(
                id="evt:support",
                timestamp=datetime(2026, 9, 1, 9, tzinfo=timezone.utc),
                actor_id="alice",
                actor_type=ActorType.HUMAN,
                source_type="note",
                source_locator="note/support",
                raw_content="supporting observation",
            ),
            RawEvent(
                id="evt:validation",
                timestamp=datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
                actor_id="bob",
                actor_type=ActorType.HUMAN,
                source_type="note",
                source_locator="note/validation",
                raw_content="independent validation observation",
            ),
        ]
    )
    return store


def _evidence(event_id: str, kind: EvidenceKind = EvidenceKind.SUPPORT) -> EvidenceRef:
    return EvidenceRef(source_event_id=event_id, evidence_kind=kind)


def _closure(**overrides) -> OperationalClosure:
    payload = {
        "id": "closure:adv-a6",
        "problem_id": "problem:adv-a6",
        "proposition": "Adopt the shared index.",
        "level": "TEAM",
        "scope": Scope(label="team:research", problem_ids=["problem:adv-a6"]),
        "supporting_evidence": [_evidence("evt:support")],
        "validation_evidence": [_evidence("evt:validation", EvidenceKind.OUTCOME)],
        "status": ClosureStatus.ACTIVE,
    }
    payload.update(overrides)
    return OperationalClosure(**payload)


def _inject(store: Store, closure: OperationalClosure) -> None:
    store.append_event(
        RawEvent(
            id="evt:inject-team-closure",
            timestamp=datetime(2035, 1, 1, tzinfo=timezone.utc),
            actor_id="attacker",
            actor_type=ActorType.TOOL,
            source_type="injection",
            source_locator="injection/team-closure",
            raw_content="injected active TEAM closure",
            metadata={"curated": {"closures": [closure.model_dump(mode="json")]}},
        )
    )


def test_active_team_closure_without_assessment_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _inject(store, _closure())
    with pytest.raises(ValueError, match="TEAM_BOUNDARY_ASSESSMENT_REQUIRED"):
        rebuild_projection(store)


def test_active_team_closure_without_validation_evidence_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assessment = BoundaryAssessment(
        included_actor_ids=("alice", "bob"),
        included_source_ids=("evt:support", "evt:validation"),
        included_problem_ids=("problem:adv-a6",),
        counterevidence_refs=(_evidence("evt:support", EvidenceKind.CONTRADICT),),
        actor_coverage=1.0,
        source_coverage=1.0,
        problem_coverage=1.0,
        uncertainty="LOW",
    )
    _inject(store, _closure(boundary_assessment=assessment, validation_evidence=[]))
    with pytest.raises(ValueError, match="TEAM_VALIDATION_BASIS_WITHOUT_EVIDENCE"):
        rebuild_projection(store)


def test_active_team_closure_with_full_boundary_evidence_is_admitted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assessment = BoundaryAssessment(
        included_actor_ids=("alice", "bob"),
        included_source_ids=("evt:support", "evt:validation"),
        included_problem_ids=("problem:adv-a6",),
        counterevidence_refs=(_evidence("evt:support", EvidenceKind.CONTRADICT),),
        actor_coverage=1.0,
        source_coverage=1.0,
        problem_coverage=1.0,
        uncertainty="LOW",
    )
    _inject(store, _closure(boundary_assessment=assessment))
    rebuild_projection(store)
    admitted = [item for item in load_closures(store) if item.id == "closure:adv-a6"]
    assert len(admitted) == 1
    assert admitted[0].status == ClosureStatus.ACTIVE
    assert admitted[0].level == "TEAM"


def test_non_team_local_closure_without_assessment_is_still_admitted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _inject(store, _closure(level="LOCAL", scope=Scope(label="local:alpha")))
    rebuild_projection(store)
    admitted = [item for item in load_closures(store) if item.id == "closure:adv-a6"]
    assert len(admitted) == 1
    assert admitted[0].status == ClosureStatus.ACTIVE
