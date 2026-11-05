"""Epoch 4 verifier correction: F-0 false green and F-2 unscored denominators.

Both defects were found by the W8 adversarial reader and are authorised for repair by the
2026-09-25 owner directive, section 6. This file is written failing-first: every assertion
below fails against the pre-correction code.

F-0: `provenance_hallucination_zero` is true by construction (the adapter builds the cited
set by filtering the already-selected set), so it measures ASSEMBLER-MANAGED citation
integrity, not model-output citation validity, and cannot fail. The repair keeps an honestly
named assembler-side invariant, adds a model-output-level check that CAN fail, and proves the
failure mode with a negative control.

F-2: an empty denominator must be UNSCORED / NOT_APPLICABLE - never 1.0 and never 0.0.
"""

from __future__ import annotations

import team_soul.eval as ev
from team_soul.contracts import EvalCase


def _case() -> EvalCase:
    return EvalCase.model_validate(
        {
            "case_id": "T-EPOCH4",
            "title": "epoch-4 probe",
            "query": "what is known",
            "expected": {},
            "events": [
                {
                    "id": "evt_a",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "actor_id": "actor-a",
                    "actor_type": "human",
                    "source_type": "note",
                    "source_locator": "note:1",
                    "raw_content": "an observation",
                }
            ],
        }
    )


def _unscored() -> str:
    return getattr(ev, "UNSCORED", "UNSCORED")


# --- F-2: an empty denominator is not a perfect score -------------------------------------


def test_empty_denominator_is_not_a_perfect_score() -> None:
    value = ev._coverage(set(), {"evt_a"})
    assert value != 1.0, "an empty expectation set must not score a perfect coverage"
    assert value == _unscored()


def test_coverage_still_measures_a_real_denominator() -> None:
    assert ev._coverage({"evt_a", "evt_b"}, {"evt_a"}) == 0.5
    assert ev._coverage({"evt_a"}, {"evt_a"}) == 1.0


def test_metrics_mark_unscored_when_nothing_could_be_measured() -> None:
    metrics = ev._metrics(
        _case(),
        arm="SOUL",
        retrieval_selected=set(),
        response_citations=set(),
        transfer=None,
        closure=None,
        projection_stable=True,
    )
    assert metrics["provider_citation_coverage"] != 1.0
    assert metrics["provider_citation_coverage"] == _unscored()
    assert metrics["problem_contamination_rate"] == _unscored()


def test_metrics_expose_scored_and_unscored_counts() -> None:
    counted = ev.scored_unscored_counts(
        {"a": 1.0, "b": _unscored(), "c": 0.0}
    )
    assert counted == {"scored": 2, "unscored": 1}


# --- F-0: the model-output-level check, and its negative control --------------------------


def test_unsupported_reference_rate_detects_an_out_of_set_citation() -> None:
    """Negative control: a claim naming evidence it was never given must be visible."""

    rate = ev.unsupported_reference_rate("I relied on evt_ghost to decide.", {"evt_real"})
    assert rate > 0.0, "an out-of-set citation must not pass silently"


def test_unsupported_reference_rate_is_zero_when_every_named_id_was_provided() -> None:
    assert ev.unsupported_reference_rate("I relied on evt_real.", {"evt_real"}) == 0.0


def test_unsupported_reference_rate_is_unscored_when_the_answer_names_nothing() -> None:
    assert ev.unsupported_reference_rate("no identifiers here", {"evt_real"}) == _unscored()


def test_assembler_binding_integrity_can_fail() -> None:
    """The structural check must fail when fed a bypassing producer."""

    assert ev.citation_binding_integrity({"evt_a"}, {"evt_a", "evt_b"}) is True
    assert ev.citation_binding_integrity({"evt_ghost"}, {"evt_a"}) is False


def test_reference_gate_is_falsifiable_and_never_vacuous() -> None:
    """The replacement gate must FAIL on a dirty row and must not PASS on nothing."""

    from team_soul import release_report as rr

    clean = rr.unsupported_references_gate([{"unsupported_reference_rate": 0.0}])
    dirty = rr.unsupported_references_gate([{"unsupported_reference_rate": 0.25}])
    vacuous = rr.unsupported_references_gate([{"unsupported_reference_rate": _unscored()}])
    legacy = rr.unsupported_references_gate([{"provenance_coverage": 1.0}])

    assert clean["status"] == "PASS"
    assert dirty["status"] == "FAIL", "a scored row citing unprovided evidence must fail"
    assert vacuous["status"].startswith("NOT OBSERVED"), (
        "present-but-nothing-scored must not render as a pass"
    )
    assert legacy["status"].startswith("NOT OBSERVED"), (
        "a report that predates the metric must not be reported as a failed invariant"
    )
