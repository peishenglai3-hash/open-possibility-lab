"""Boundary and schema guards for the second historical corpus (corpus2:honghu).

The three C2X* candidates encode real episodes of an owner-authorised external project and
are deliberately left at ``human_approved: false``: approval is the owner act, not an
executor act. These tests hold the supporting machinery honest -- schema validity,
expectation consistency, locator resolution, and the read-only access declaration.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from team_soul.contracts import ClosureStatus
from team_soul.ingest import load_fixture
from team_soul.scenario import _E2_ENVELOPE_KEYS, load_historical_replay

REPO = Path(__file__).resolve().parents[1]
IN_REPO = REPO / "e2_candidates"
CORPUS2 = REPO / "e2_candidates_corpus2"
CORPUS2_ROOT_ENV = "TEAM_SOUL_CORPUS2_ROOT"

if not CORPUS2.is_dir():
    pytest.skip(
        "authorised corpus-2 is not distributed in the public snapshot",
        allow_module_level=True,
    )


def _load_locator_tool():
    spec = importlib.util.spec_from_file_location(
        "verify_e2_locators", REPO / "tools" / "verify_e2_locators.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_e2_locators"] = module
    spec.loader.exec_module(module)
    return module


def _cases() -> list[Path]:
    return sorted(CORPUS2.glob("C*.json"))


def test_corpus2_directory_holds_three_minimal_episodes() -> None:
    assert len(_cases()) == 3, "keep the corpus-2 episode set minimal by design"


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.name)
def test_corpus2_candidate_validates_as_an_eval_case(path: Path) -> None:
    case = load_fixture(path, ignore_keys=_E2_ENVELOPE_KEYS)
    assert case.case_id == json.loads(path.read_text(encoding="utf-8"))["case_id"]
    assert len(case.events) >= 5
    ids = {event.id for event in case.events}
    for key in ("required_event_ids", "temporal_event_ids", "dissent_event_ids"):
        values = set(case.expected.get(key, []) or [])
        assert values <= ids, path.name + ": " + key + " names an unknown event"
    assert case.expected["closure_status"] in {item.value for item in ClosureStatus}


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.name)
def test_corpus2_records_when_events_were_recorded(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    stamps = [event["timestamp"] for event in payload["events"]]
    assert stamps == sorted(stamps), path.name + ": ledger order must follow record time"
    assert all(stamp.endswith("Z") for stamp in stamps), path.name + ": UTC canonical"


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.name)
def test_corpus2_access_boundary_is_read_only(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    access = payload["provenance"]["access"]
    assert access["mode"] == "read-only"
    assert access["parent_traversal"] is False
    assert access["copied_into_repo"] is False
    assert payload["permission_boundary"]["owner_review_required"] is True
    assert payload["permission_boundary"]["contains_credentials"] is False


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.name)
def test_corpus2_locators_resolve_against_the_authorised_root(path: Path) -> None:
    root = os.environ.get(CORPUS2_ROOT_ENV)
    if not root or not Path(root).is_dir():
        pytest.skip("authorised corpus-2 root not reachable in this environment")
    module = _load_locator_tool()
    report = module.check_candidate(path, {"": REPO, "corpus2": Path(root)})
    assert report["problems"] == [], path.name + ": " + str(report["problems"])
    assert report["files_checked"] > 0


def test_case_ids_are_unique_across_both_corpora() -> None:
    ids = []
    for directory in (IN_REPO, CORPUS2):
        for path in sorted(directory.glob("C*.json")):
            ids.append(json.loads(path.read_text(encoding="utf-8"))["case_id"])
    assert len(ids) == len(set(ids))


def test_corpus2_material_is_not_loadable_until_the_owner_approves() -> None:
    cases, status = load_historical_replay(CORPUS2)
    approved = [
        path for path in _cases()
        if json.loads(path.read_text(encoding="utf-8")).get("human_approved")
    ]
    assert status["accepted_cases"] == len(approved)
    assert len(cases) == len(approved)
    if not approved:
        assert status["status"] == "NOT OBSERVED / OPEN GAP"
