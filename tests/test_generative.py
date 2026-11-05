from pathlib import Path

import pytest

from team_soul.contracts import EvidenceKind, EvidenceRef, Scope
from team_soul.generative import (
    GenerativeCandidate,
    GenerativeCandidateStatus,
    GenerativeCandidateType,
    persist_candidate,
    reject_illegal_promotion,
    validate_candidate,
)
from team_soul.ingest import load_fixture
from team_soul.projection import load_claims, load_closures, rebuild_projection
from team_soul.storage import Store


def test_speculative_candidate_is_persisted_as_candidate_not_fact_or_closure(tmp_path: Path) -> None:
    case = load_fixture(Path("fixtures/C03_dissent_preservation.json"))
    store = Store(tmp_path / "generative.sqlite3")
    store.init_schema()
    store.append_events(case.events)
    rebuild_projection(store)
    candidate = GenerativeCandidate.create(
        candidate_type=GenerativeCandidateType.RELATION,
        proposition="The privacy objection may connect the memory-index problem to transfer policy.",
        source_event_ids=("evt_c03_1", "evt_c03_2"),
        generation_context_hash="ctx:generative:test",
        scope=Scope(project_ids=["alpha", "beta"], problem_ids=["problem:c03_shared_memory"]),
    )

    persist_candidate(store, candidate)
    rebuilt = GenerativeCandidate.model_validate(
        store.get_event(f"evt:{candidate.id}").metadata["generative_candidate"]["candidate"]
    )
    validated = validate_candidate(
        candidate,
        (EvidenceRef(source_event_id="evt_c03_3", evidence_kind=EvidenceKind.OUTCOME),),
    )

    assert rebuilt.status == GenerativeCandidateStatus.UNVALIDATED
    assert validated.status == GenerativeCandidateStatus.VALIDATED
    assert not any(claim.id == candidate.id for claim in load_claims(store))
    assert not any(closure.id == candidate.id for closure in load_closures(store))
    with pytest.raises(PermissionError, match="cannot be promoted directly"):
        reject_illegal_promotion(candidate, "TEAM_STATE")
    store.close()
