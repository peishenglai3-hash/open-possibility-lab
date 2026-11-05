import json
from pathlib import Path

from team_soul.config import RuntimeConfig
from team_soul.eval_v02 import run_v02_suite
from team_soul.scenario import (
    AblationKind,
    EvidenceLevel,
    ScenarioEnvironment,
    ablate_case,
    build_scenario_matrix,
    load_historical_replay,
    scenario_manifest,
)

# The six missing E3 perturbation families the epoch-2 handoff requires, one
# representative case per family, with the fixture each one perturbs.
REQUIRED_E3_FAMILIES: tuple[tuple[str, str], ...] = (
    ("future_leak", "C01"),
    ("false_summary", "C01"),
    ("noise_reopen", "C01"),
    ("majority_over_dissent", "C03"),
    ("invalid_transfer_high_similarity", "C04"),
    ("fake_consensus", "C05"),
)


def test_d5_scenario_matrix_is_deterministic_and_covers_required_mechanisms() -> None:
    first = build_scenario_matrix("fixtures")
    second = build_scenario_matrix("fixtures")
    assert len(first) == 32
    assert [item.spec.scenario_id for item in first] == [item.spec.scenario_id for item in second]
    assert scenario_manifest(first)["manifest_hash"] == scenario_manifest(second)["manifest_hash"]
    assert sum(item.spec.evidence_level == EvidenceLevel.E1 for item in first) == 5
    assert sum(item.spec.evidence_level == EvidenceLevel.E3 for item in first) == 27
    assert sum(item.spec.environment == ScenarioEnvironment.PERTURBATION for item in first) == 27
    assert {item.spec.mechanism for item in first} >= {
        "temporal_revision",
        "scope_and_dissent",
        "dissent_preservation",
        "guarded_transfer",
        "closure_and_unknown",
        "unknown_evidence",
    }
    families = {family for family, _fixture in REQUIRED_E3_FAMILIES}
    coverage = {
        item.spec.perturbation: item
        for item in first
        if item.spec.perturbation in families
    }
    # Every required family is present exactly once, on its declared fixture, as
    # an E3 Perturbation case inside the source fixture episode.
    assert set(coverage) == families
    for family, fixture in REQUIRED_E3_FAMILIES:
        item = coverage[family]
        assert sum(other.spec.perturbation == family for other in first) == 1
        assert item.spec.source_case_id == fixture
        assert item.spec.evidence_level == EvidenceLevel.E3
        assert item.spec.environment == ScenarioEnvironment.PERTURBATION
        assert item.spec.tags == ("adversarial", family, item.spec.mechanism)
        assert item.spec.episode_id == f"fixture:{fixture}"


def test_d5_perturbations_and_ablations_do_not_mutate_source_cases() -> None:
    matrix = build_scenario_matrix("fixtures")
    source = next(item for item in matrix if item.spec.scenario_id == "SYN-C03")
    source_ids = [event.id for event in source.case.events]
    perturbed = next(item for item in matrix if item.spec.perturbation == "drop_dissent" and item.spec.source_case_id == "C03")
    assert [event.id for event in source.case.events] == source_ids
    assert len(perturbed.case.events) < len(source.case.events)
    ablated = ablate_case(source, AblationKind.NO_DISSENT)
    assert ablated.case.case_id.endswith("__ABLATE__no-dissent")
    assert [event.id for event in source.case.events] == source_ids


def test_d5_historical_replay_requires_explicit_e2_authority(tmp_path: Path) -> None:
    cases, status = load_historical_replay(tmp_path / "missing-history")
    assert cases == []
    assert status["status"] == "NOT OBSERVED / OPEN GAP"
    case_path = tmp_path / "C99_unqualified.json"
    case_path.write_text(
        json.dumps({"case_id": "C99", "title": "synthetic", "query": "q", "events": []}),
        encoding="utf-8",
    )
    cases, status = load_historical_replay(tmp_path)
    assert cases == []
    assert status["rejected_unqualified_cases"] == 1
    assert status["status"] == "NOT OBSERVED / OPEN GAP"


def test_d5_runner_emits_scorecard_manifest_and_ablation_report(tmp_path: Path) -> None:
    report = run_v02_suite(
        "fixtures",
        RuntimeConfig(data_dir=tmp_path / "runtime"),
        output_json=tmp_path / "EVAL_RESULTS_V02.json",
        output_markdown=tmp_path / "EVAL_REPORT_V02.md",
        output_manifest=tmp_path / "SCENARIO_MATRIX_V02.json",
        arms=("BASE", "RAG", "SOUL"),
        scenario_limit=3,
    )
    assert report["schema_version"] == "eval.v0.2.d5"
    assert report["scenario_matrix"]["declared_case_count"] == 32
    assert report["gates"]["scenario_target_24_to_32"] is True
    assert report["gates"]["historical_replay_evidence"] == "NOT OBSERVED / OPEN GAP"
    assert report["gates"]["ablation_runner"] is True
    assert report["ablations"]["case_count"] == 15
    assert set(report["scorecard"]["by_arm"]) == {"BASE", "RAG", "SOUL"}
    assert json.loads((tmp_path / "SCENARIO_MATRIX_V02.json").read_text(encoding="utf-8"))["case_count"] == 32
    assert "NOT OBSERVED / OPEN GAP" in (tmp_path / "EVAL_REPORT_V02.md").read_text(encoding="utf-8")
    by_scenario: dict[str, list[dict]] = {}
    for row in report["ablations"]["cases"]:
        assert row["ablation_input_is_frozen"] is True
        by_scenario.setdefault(row["scenario_id"], []).append(row)
    for rows in by_scenario.values():
        assert len({row["input_case_id"] for row in rows}) == 1
        assert len({row["input_history_hash"] for row in rows}) == 1
        assert len({tuple(row["input_event_ids"]) for row in rows}) == 1
