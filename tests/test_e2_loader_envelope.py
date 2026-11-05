"""E2 replay material must be loadable once the owner approves it.

Found while preparing e2_candidates/: load_historical_replay requires top-level
evidence_level/human_approved (plus provenance/permission_boundary) but then called
load_fixture, whose EvalCase forbids extra fields. Approving any candidate therefore made
the loader raise ValidationError. The envelope is now validated by the loader and stripped
before schema validation.
"""

from __future__ import annotations

import json
from pathlib import Path

from team_soul.scenario import load_historical_replay

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "C01_historical_revision.json"


def _candidate(directory: Path, *, approved: bool) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["case_id"] = "C90"
    payload["evidence_level"] = "E2"
    payload["human_approved"] = approved
    payload["provenance"] = {"source": "unit-test", "locator": "fixtures/C01", "retrieved_at": "2026-09-24"}
    payload["permission_boundary"] = {"classification": "public-repo-internal"}
    (directory / "C90_approved_probe.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_unapproved_e2_material_is_still_rejected(tmp_path: Path) -> None:
    _candidate(tmp_path, approved=False)
    cases, status = load_historical_replay(tmp_path)
    assert cases == []
    assert status["accepted_cases"] == 0
    assert status["rejected_unqualified_cases"] == 1


def test_approved_e2_material_loads(tmp_path: Path) -> None:
    _candidate(tmp_path, approved=True)
    cases, status = load_historical_replay(tmp_path)
    assert status["status"] == "PASS"
    assert status["accepted_cases"] == 1
    assert len(cases) == 1
    assert cases[0].spec.scenario_id == "HIST-C90"
    assert cases[0].spec.evidence_level == "E2"
