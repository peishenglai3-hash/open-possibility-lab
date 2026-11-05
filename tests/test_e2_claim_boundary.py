"""Guard the E2 historical-replay claim boundary (owner item A, C71 narrowing).

The C71 episode contains the repository own supplied synthetic fixture narrative
(batch size 64 -> failure -> 32). That material is E1-class and must never be counted
as E2 capability evidence. These tests make the exclusion machine-checked instead of
prose-only, and re-verify every cited locator so approval can never outrun provenance.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CANDIDATES = REPO / "e2_candidates"

if not CANDIDATES.is_dir():
    pytest.skip(
        "authorised E2 candidates are not distributed in the public snapshot",
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


def _payloads() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(CANDIDATES.glob("C*.json"))
    ]


def _event_level_e1(payload: dict) -> set[str]:
    return {
        event["id"]
        for event in payload.get("events", [])
        if event.get("metadata", {}).get("evidence_class") == "E1"
    }


def test_approved_candidates_exist_and_are_approved() -> None:
    approved = [p for _, p in _payloads() if p.get("human_approved")]
    assert len(approved) >= 8, "V03-BOUNDARY.md:31 requires at least 8 approved E2 cases"


def test_declared_e1_events_are_never_e2_capability_targets() -> None:
    for path, payload in _payloads():
        if not payload.get("human_approved"):
            continue
        expected = payload.get("expected", {})
        e1 = _event_level_e1(payload) | set(expected.get("e1_class_event_ids", []) or [])
        for key in ("required_event_ids", "temporal_event_ids", "dissent_event_ids"):
            overlap = e1.intersection(expected.get(key, []) or [])
            message = path.name + ": " + key + " claims E1-class events " + str(sorted(overlap))
            assert not overlap, message


def test_e1_markers_never_appear_inside_curated_claims() -> None:
    for path, payload in _payloads():
        if not payload.get("human_approved"):
            continue
        for event in payload.get("events", []):
            curated = (event.get("metadata") or {}).get("curated") or {}
            for claim in curated.get("claims", []) or []:
                message = path.name + ": curated claims are projected into Claim, which forbids extra fields"
                assert "evidence_class" not in claim, message


def test_event_level_and_expected_level_e1_sets_agree() -> None:
    for path, payload in _payloads():
        if not payload.get("human_approved"):
            continue
        listed = set(payload.get("expected", {}).get("e1_class_event_ids", []) or [])
        message = path.name + ": event-level and expected-level E1 declarations differ"
        assert _event_level_e1(payload) == listed, message


@pytest.mark.parametrize("path", sorted(CANDIDATES.glob("C*.json")), ids=lambda p: p.name)
def test_every_cited_locator_resolves(path: Path) -> None:
    module = _load_locator_tool()
    report = module.check_candidate(path)
    problems = str(report["problems"])
    assert report["problems"] == [], path.name + ": " + problems
    assert report["files_checked"] > 0


def test_approved_e2_cases_declare_provenance_and_permission_boundary() -> None:
    for path, payload in _payloads():
        if not payload.get("human_approved"):
            continue
        provenance = payload.get("provenance", {})
        assert provenance.get("source"), path.name
        assert provenance.get("locator"), path.name
        assert provenance.get("retrieved_at"), path.name
        boundary = payload.get("permission_boundary", {})
        assert boundary.get("classification"), path.name
        assert boundary.get("contains_private_corpus") is False, path.name
        assert boundary.get("contains_credentials") is False, path.name
