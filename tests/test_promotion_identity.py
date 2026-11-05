"""FR-07 regression: promotion evidence must not be inflatable by relabelling samples.

Reported by the fresh adversarial review (A7): deduplicate_pairs grouped only by the declared
episode_id while the content-derived sample_identity was unused, so eight rows of ONE sample
carrying eight different declared episode ids counted as eight unique episodes and could reach
PASS/ESCALATE (PROMOTE when human-approved).

Invariants under test:
  - rows of one content sample cannot manufacture a larger effective N by relabelling;
  - derived variants (perturbations/ablations) of one source trajectory stay one unit.
"""

from __future__ import annotations

from team_soul.kernel.promotion import HeldoutPair, deduplicate_pairs

METRICS = {"provenance_coverage": 1.0}


def _pair(scenario_id: str, episode_id: str, sample_identity: str) -> HeldoutPair:
    return HeldoutPair(
        scenario_id=scenario_id,
        evidence_level="E3",
        baseline_metrics=dict(METRICS),
        candidate_metrics=dict(METRICS),
        episode_id=episode_id,
        sample_identity=sample_identity,
    )


def test_relabelled_copies_of_one_sample_collapse_to_one_episode() -> None:
    pairs = [
        _pair(f"PER-C01-copy-{index}", f"episode:declared-{index}", "content:one-sample")
        for index in range(8)
    ]
    canonical, summary = deduplicate_pairs(pairs)
    assert summary["unique_episode_count"] == 1
    assert len(canonical) == 1


def test_derived_variants_of_one_trajectory_stay_one_unit() -> None:
    pairs = [
        _pair("SYN-C01", "fixture:C01", "content:syn"),
        _pair("PER-C01-drop_dissent", "fixture:C01", "content:drop"),
        _pair("PER-C01-inject_conflict", "fixture:C01", "content:conflict"),
    ]
    canonical, summary = deduplicate_pairs(pairs)
    assert summary["unique_episode_count"] == 1
    assert len(canonical) == 1


def test_distinct_samples_remain_distinct_episodes() -> None:
    pairs = [
        _pair("SYN-C01", "fixture:C01", "content:c01"),
        _pair("SYN-C02", "fixture:C02", "content:c02"),
    ]
    canonical, summary = deduplicate_pairs(pairs)
    assert summary["unique_episode_count"] == 2
    assert len(canonical) == 2


def test_relabelled_copy_is_merged_with_its_source_episode() -> None:
    pairs = [
        _pair("SYN-C01", "fixture:C01", "content:c01"),
        _pair("PER-C01-drop_dissent", "fixture:C01", "content:drop"),
        _pair("PER-C01-copy", "episode:relabelled", "content:drop"),
    ]
    canonical, summary = deduplicate_pairs(pairs)
    assert summary["unique_episode_count"] == 1
    assert len(canonical) == 1
