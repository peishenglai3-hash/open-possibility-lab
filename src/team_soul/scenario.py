from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import ActorType, Contract, EvalCase, RawEvent
from .hashing import sha256_json
from .ingest import load_fixture, load_fixtures


class EvidenceLevel(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"


class ScenarioEnvironment(StrEnum):
    SYNTHETIC = "Synthetic"
    HISTORICAL_REPLAY = "Historical Replay"
    PERTURBATION = "Perturbation"
    LIVE_PILOT = "Live Pilot"


class AblationKind(StrEnum):
    NO_DISSENT = "no-dissent"
    NO_TEMPORAL = "no-temporal"
    NO_TRANSFER = "no-transfer"
    NO_CLOSURE = "no-closure"
    NO_PROBLEM = "no-problem"


class ScenarioSpec(Contract):
    scenario_id: str
    source_case_id: str
    mechanism: str
    environment: ScenarioEnvironment
    evidence_level: EvidenceLevel
    perturbation: str | None = None
    # Evaluation identity is deliberately separate from the display/scenario
    # row id. All perturbations, ablations, and rewritten variants of one
    # source trajectory share an episode id and therefore cannot leak across
    # train/held-out or inflate promotion N.
    episode_id: str = ""
    variant_of: str | None = None
    sample_identity: str = ""
    tags: tuple[str, ...] = ()


class ScenarioCase(Contract):
    spec: ScenarioSpec
    case: EvalCase


#: Top-level keys required by the E2 envelope. They are declared by the replay material
#: itself and stripped before EvalCase validation.
_E2_ENVELOPE_KEYS = ("evidence_level", "human_approved", "provenance", "permission_boundary")

_MECHANISMS = {
    "C01": "temporal_revision",
    "C02": "scope_and_dissent",
    "C03": "dissent_preservation",
    "C04": "guarded_transfer",
    "C05": "closure_and_unknown",
}
_PERTURBATIONS = (
    "reverse_event_order",
    "drop_dissent",
    "multi_problem",
    "inject_conflict",
)

# E3 coverage families required by the handoff that the epoch-1 matrix lacked.
# Exactly one representative case per family keeps the frozen 24..32 scenario
# gate intact, so the six families add six cases, not thirty.
_E3_COVERAGE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("C01", "future_leak"),
    ("C01", "false_summary"),
    ("C01", "noise_reopen"),
    ("C03", "majority_over_dissent"),
    ("C04", "invalid_transfer_high_similarity"),
    ("C05", "fake_consensus"),
)


def build_scenario_matrix(fixture_dir: str | Path) -> list[ScenarioCase]:
    """Build the frozen 32-case D5 matrix from the five V0.1 fixtures.

    The five source cases remain unchanged. Twenty deterministic E3
    perturbations cover order, dissent, actor distribution, and conflict. One
    additional unknown-evidence perturbation makes the unknown mechanism
    explicit. Six further E3 cases complete the required perturbation coverage:
    one representative case each for future_leak, false_summary, noise_reopen,
    majority_over_dissent, invalid_transfer_high_similarity and fake_consensus.
    No transformed case is labelled historical evidence.
    """

    bases = load_fixtures(fixture_dir)
    if not bases:
        raise ValueError(f"no C*.json fixtures found in {fixture_dir}")
    cases: list[ScenarioCase] = []
    for base in bases:
        mechanism = _MECHANISMS.get(base.case_id, "unclassified")
        cases.append(
            _scenario_case(
                base,
                scenario_id=f"SYN-{base.case_id}",
                mechanism=mechanism,
                environment=ScenarioEnvironment.SYNTHETIC,
                evidence_level=EvidenceLevel.E1,
                episode_id=f"fixture:{base.case_id}",
                tags=("v0.1-source", mechanism),
            )
        )
        perturbations = _PERTURBATIONS
        if base.case_id != "C01":
            perturbations = (*_PERTURBATIONS[:2], "actor_imbalance", _PERTURBATIONS[3])
        for perturbation in perturbations:
            events = _perturb_events(base, perturbation)
            cases.append(
                _scenario_case(
                    base,
                    scenario_id=f"PER-{base.case_id}-{perturbation}",
                    mechanism=mechanism,
                    environment=ScenarioEnvironment.PERTURBATION,
                    evidence_level=EvidenceLevel.E3,
                    perturbation=perturbation,
                    events=events,
                    expected=expected_for_perturbed_corpus(base, events),
                    episode_id=f"fixture:{base.case_id}",
                    variant_of=f"SYN-{base.case_id}",
                    tags=("adversarial", perturbation, mechanism),
                )
            )
    c05 = next((case for case in bases if case.case_id == "C05"), None)
    if c05 is not None:
        cases.append(
            _scenario_case(
                c05,
                scenario_id="PER-C05-inject-unknown",
                mechanism="unknown_evidence",
                environment=ScenarioEnvironment.PERTURBATION,
                evidence_level=EvidenceLevel.E3,
                perturbation="inject_unknown",
                events=_perturb_events(c05, "inject_unknown"),
                episode_id="fixture:C05",
                variant_of="SYN-C05",
                tags=("adversarial", "unknown_evidence"),
            )
        )
    by_case_id = {base.case_id: base for base in bases}
    for source_case_id, family in _E3_COVERAGE_FAMILIES:
        base = by_case_id.get(source_case_id)
        if base is None:
            raise ValueError(
                f"E3 coverage family {family!r} requires fixture {source_case_id}"
            )
        mechanism = _MECHANISMS.get(source_case_id, "unclassified")
        events = _perturb_events(base, family)
        cases.append(
            _scenario_case(
                base,
                scenario_id=f"PER-{source_case_id}-{family}",
                mechanism=mechanism,
                environment=ScenarioEnvironment.PERTURBATION,
                evidence_level=EvidenceLevel.E3,
                perturbation=family,
                events=events,
                # Each perturbation must not change the correct outcome, so the
                # base expectations stay in force. The helper only rewrites them
                # when a transform removed evidence from the corpus.
                expected=expected_for_perturbed_corpus(base, events),
                episode_id=f"fixture:{source_case_id}",
                variant_of=f"SYN-{source_case_id}",
                tags=("adversarial", family, mechanism),
                # future_leak is the only family whose invariant depends on a real
                # decision time; every other case keeps decision_time = None.
                decision_time=(
                    _base_decision_time(base) if family == "future_leak" else None
                ),
            )
        )
    return cases


def load_historical_replay(
    directory: str | Path | None,
) -> tuple[list[ScenarioCase], dict[str, Any]]:
    """Load only explicitly human-approved E2 replay cases.

    A directory containing ordinary synthetic fixtures is not promoted to E2.
    Each accepted JSON must declare top-level ``evidence_level: E2`` and
    ``human_approved: true``. Missing or unqualified material is reported, not
    silently relabelled.
    """

    if directory is None or not Path(directory).exists():
        return [], {
            "status": "NOT OBSERVED / OPEN GAP",
            "accepted_cases": 0,
            "reason": "no historical replay directory was supplied",
        }
    accepted: list[ScenarioCase] = []
    rejected = 0
    for path in sorted(Path(directory).glob("C*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("evidence_level") != EvidenceLevel.E2.value or not payload.get(
            "human_approved", False
        ):
            rejected += 1
            continue
        # E2 envelope keys are loader metadata; EvalCase forbids extra fields, so they
        # are stripped before schema validation (otherwise approving a case would make
        # the loader raise).
        case = load_fixture(path, ignore_keys=_E2_ENVELOPE_KEYS)
        accepted.append(
            _scenario_case(
                case,
                scenario_id=f"HIST-{case.case_id}",
                mechanism="historical_replay",
                environment=ScenarioEnvironment.HISTORICAL_REPLAY,
                evidence_level=EvidenceLevel.E2,
                episode_id=f"historical:{case.case_id}",
                tags=("human-approved", "historical-replay"),
            )
        )
    if not accepted:
        return [], {
            "status": "NOT OBSERVED / OPEN GAP",
            "accepted_cases": 0,
            "rejected_unqualified_cases": rejected,
            "reason": "no human-approved E2 replay case was available",
        }
    return accepted, {
        "status": "PASS",
        "accepted_cases": len(accepted),
        "rejected_unqualified_cases": rejected,
    }


def scenario_manifest(cases: list[ScenarioCase]) -> dict[str, Any]:
    entries = [
        {
            **item.spec.model_dump(mode="json"),
            "episode_id": _episode_id(item),
            "sample_identity": _sample_identity(item),
            "event_ids": [event.id for event in item.case.events],
            "event_count": len(item.case.events),
        }
        for item in cases
    ]
    return {
        "schema_version": "scenario-matrix.v0.2",
        "case_count": len(entries),
        "cases": entries,
        "manifest_hash": sha256_json(entries),
    }


def ablate_case(case: ScenarioCase, kind: AblationKind | str) -> ScenarioCase:
    """Apply a named mechanism ablation without changing source fixtures."""

    ablation = AblationKind(kind)
    events = list(case.case.events)
    transfer = case.case.transfer
    closure = case.case.closure_proposal
    if ablation == AblationKind.NO_DISSENT:
        filtered = [event for event in events if not _is_dissent(event)]
        events = filtered or events[:1]
    elif ablation == AblationKind.NO_TEMPORAL:
        events = [_without_roles(event, {"old", "historical", "temporal", "revision"}) for event in events]
    elif ablation == AblationKind.NO_TRANSFER:
        transfer = None
        events = [_without_roles(event, {"transfer", "transfer_failure", "failure"}) for event in events]
    elif ablation == AblationKind.NO_CLOSURE:
        closure = None
        events = [_without_roles(event, {"closure", "unresolved", "reopen"}) for event in events]
    elif ablation == AblationKind.NO_PROBLEM:
        events = [_without_problem_projection(event) for event in events]
    payload = case.case.model_dump(mode="json")
    payload.update(
        {
            "case_id": f"{case.case.case_id}__ABLATE__{ablation.value}",
            "title": f"{case.case.title} [{ablation.value}]",
            "events": [event.model_dump(mode="json") for event in events],
            "transfer": transfer.model_dump(mode="json") if transfer else None,
            "closure_proposal": closure.model_dump(mode="json") if closure else None,
        }
    )
    return ScenarioCase(
        spec=case.spec.model_copy(
            update={
                "scenario_id": f"{case.spec.scenario_id}__ABLATE__{ablation.value}",
                "perturbation": ablation.value,
                "variant_of": case.spec.scenario_id,
                "tags": (*case.spec.tags, "ablation", ablation.value),
            }
        ),
        case=EvalCase.model_validate(payload),
    )


def _scenario_case(
    base: EvalCase,
    *,
    scenario_id: str,
    mechanism: str,
    environment: ScenarioEnvironment,
    evidence_level: EvidenceLevel,
    perturbation: str | None = None,
    events: list[RawEvent] | None = None,
    expected: dict[str, Any] | None = None,
    episode_id: str | None = None,
    variant_of: str | None = None,
    tags: tuple[str, ...] = (),
    decision_time: datetime | None = None,
) -> ScenarioCase:
    payload = base.model_dump(mode="json")
    payload["case_id"] = scenario_id
    payload["title"] = f"{base.title} [{scenario_id}]"
    if events is not None:
        payload["events"] = [event.model_dump(mode="json") for event in events]
    if expected is not None:
        payload["expected"] = expected
    if decision_time is not None:
        payload["decision_time"] = decision_time
    return ScenarioCase(
        spec=ScenarioSpec(
            scenario_id=scenario_id,
            source_case_id=base.case_id,
            mechanism=mechanism,
            environment=environment,
            evidence_level=evidence_level,
            perturbation=perturbation,
            episode_id=episode_id or f"episode:{base.case_id}",
            variant_of=variant_of,
            tags=tags,
        ),
        case=EvalCase.model_validate(payload),
    )


def expected_for_perturbed_corpus(base: EvalCase, events: list[RawEvent]) -> dict[str, Any] | None:
    """Derive expectations that describe the perturbed corpus instead of the source case.

    P-01: a perturbation that deletes evidence must not leave that evidence inside the
    metric denominator. Provenance coverage is only meaningful for events that still
    exist, so removed ids are dropped from the expectation sets and recorded explicitly
    in removed_expected_event_ids for audit. Returns None when nothing changed.
    """

    remaining = {event.id for event in events}
    expected = copy.deepcopy(base.expected)
    removed: list[str] = []
    changed = False
    for key in (
        "required_event_ids",
        "temporal_event_ids",
        "allowed_event_ids",
        "dissent_event_ids",
    ):
        values = expected.get(key)
        if not isinstance(values, list):
            continue
        kept = [event_id for event_id in values if event_id in remaining]
        if kept != values:
            changed = True
        removed.extend(event_id for event_id in values if event_id not in remaining)
        expected[key] = kept
    if not changed:
        return None
    expected["removed_expected_event_ids"] = sorted(set(removed))
    return expected


def _episode_id(scenario: ScenarioCase) -> str:
    return scenario.spec.episode_id or f"episode:{scenario.spec.source_case_id}"


def _sample_identity(scenario: ScenarioCase) -> str:
    return scenario.spec.sample_identity or sha256_json(
        {
            # FR-07: content identity only. The declared episode label is deliberately
            # excluded so that relabelled copies of one sample share this key.
            "query": scenario.case.query,
            "event_ids": [event.id for event in scenario.case.events],
            "event_hashes": [event.content_hash for event in scenario.case.events],
        }
    )


def _perturb_events(case: EvalCase, perturbation: str) -> list[RawEvent]:
    events = list(case.events)
    if perturbation == "reverse_event_order":
        return list(reversed(events))
    if perturbation == "drop_dissent":
        dissent = [event for event in events if _is_dissent(event)]
        target = dissent[-1] if dissent and len(events) > 1 else events[-1]
        return [event for event in events if event.id != target.id] or events[:1]
    if perturbation == "actor_imbalance":
        actor_id = events[0].actor_id
        return [event.model_copy(update={"actor_id": actor_id}) for event in events]
    if perturbation == "multi_problem":
        first = events[0]
        problem_hints = list(first.problem_hints)
        if "problem:secondary" not in problem_hints:
            problem_hints.append("problem:secondary")
        return [first.model_copy(update={"problem_hints": problem_hints}), *events[1:]]
    if perturbation in {"inject_conflict", "inject_unknown"}:
        last = events[-1]
        label = "conflict" if perturbation == "inject_conflict" else "unknown"
        metadata = {
            "roles": ["perturbation", label],
            "evidence_level": EvidenceLevel.E3.value,
        }
        extra = RawEvent(
            id=f"{last.id}:perturb:{label}",
            timestamp=last.timestamp + timedelta(seconds=1),
            actor_id=f"perturbation:{label}",
            actor_type=ActorType.TOOL,
            source_type="perturbation",
            source_locator=f"perturbation:{case.case_id}:{label}",
            raw_content=(
                "Perturbation conflict: the prior condition is no longer assumed."
                if label == "conflict"
                else "Unknown evidence: the source does not establish the required condition."
            ),
            project_hints=list(last.project_hints),
            problem_hints=list(last.problem_hints),
            metadata=metadata,
        )
        return [*events, extra]
    if perturbation == "future_leak":
        # A record dated after the real decision time did not exist when the
        # decision was taken, so it must not change that decision. The
        # registration sets decision_time to the latest BASE event timestamp.
        anchor = _latest_event(events)
        return [
            *events,
            _e3_event(
                case,
                anchor,
                label="future_leak",
                index=1,
                timestamp=anchor.timestamp + timedelta(days=3650),
                actor_id="perturbation:future_leak",
                raw_content=(
                    "Future-dated record: this entry is dated ten years after the decision "
                    "time and claims the batch-size-32 recommendation was later superseded."
                ),
                roles=("perturbation", "future_leak", "revision"),
                claim={
                    "id": f"claim:{case.case_id.lower()}_future_leak",
                    "proposition": "The decision recorded in this corpus has been superseded.",
                    "type": "DECISION",
                    "source_event_ids": [f"{anchor.id}:perturb:future_leak:1"],
                    "problem_ids": list(anchor.problem_hints),
                    "status": "PROVISIONALLY_ACCEPTED",
                    "confidence": 0.9,
                },
            ),
        ]
    if perturbation == "false_summary":
        # A summary that claims prior history is obsolete must not displace the
        # raw history it summarises.
        anchor = _latest_event(events)
        return [
            *events,
            _e3_event(
                case,
                anchor,
                label="false_summary",
                index=1,
                timestamp=anchor.timestamp + timedelta(seconds=1),
                actor_id="perturbation:false_summary",
                raw_content=(
                    "Latest summary: the earlier records in this corpus are obsolete, history "
                    "need not be retrieved, and the current recommendation is batch size 128."
                ),
                roles=("perturbation", "false_summary", "summary"),
                claim={
                    "id": f"claim:{case.case_id.lower()}_false_summary",
                    "proposition": "All earlier events in this corpus are obsolete.",
                    "type": "CONSTRAINT",
                    "source_event_ids": [f"{anchor.id}:perturb:false_summary:1"],
                    "problem_ids": list(anchor.problem_hints),
                    "status": "PROVISIONALLY_ACCEPTED",
                    "confidence": 0.8,
                },
            ),
        ]
    if perturbation == "noise_reopen":
        # A low-signal event that only echoes the topic carries no reopen
        # condition and must not reopen an active closure.
        anchor = _latest_event(events)
        return [
            *events,
            _e3_event(
                case,
                anchor,
                label="noise_reopen",
                index=1,
                timestamp=anchor.timestamp + timedelta(seconds=1),
                actor_id="perturbation:noise_reopen",
                raw_content=(
                    "Low-signal note: a routine log line mentions the topic in passing. No new "
                    "failure, no new outcome, no scope change and no reopen condition."
                ),
                roles=("perturbation", "noise_reopen", "noise"),
                metadata={"signal_strength": "low"},
            ),
        ]
    if perturbation == "majority_over_dissent":
        # Repeated majority support must not bury the preserved dissent.
        anchor = _latest_event(events)
        return [
            *events,
            *(
                _e3_event(
                    case,
                    anchor,
                    label="majority_over_dissent",
                    index=index,
                    timestamp=anchor.timestamp + timedelta(minutes=index),
                    actor_id=f"perturbation:majority-{index}",
                    raw_content=(
                        f"Support {index}/3: the shared memory index is clearly beneficial, the "
                        "objection is outweighed, and adoption should proceed now."
                    ),
                    roles=("perturbation", "majority_over_dissent", "support", "direct"),
                    claim={
                        "id": f"claim:{case.case_id.lower()}_majority_{index}",
                        "proposition": (
                            "The shared memory index should be adopted despite the objection."
                        ),
                        "type": "DECISION",
                        "source_event_ids": [
                            f"{anchor.id}:perturb:majority_over_dissent:{index}"
                        ],
                        "problem_ids": list(anchor.problem_hints),
                        "status": "PROVISIONALLY_ACCEPTED",
                        "confidence": 0.8,
                    },
                )
                for index in range(1, 4)
            ),
        ]
    if perturbation == "invalid_transfer_high_similarity":
        # Added surface similarity is not evidence that a forbidden condition
        # stopped applying, so the transfer decision must stay REJECT.
        anchor = _latest_event(events)
        return [
            *events,
            *(
                _e3_event(
                    case,
                    anchor,
                    label="invalid_transfer_high_similarity",
                    index=index,
                    timestamp=anchor.timestamp + timedelta(minutes=index),
                    actor_id=f"perturbation:similarity-{index}",
                    raw_content=(
                        f"Similarity {index}/3: the alpha caching policy and the beta real-time "
                        "telemetry policy are near-identical in wording, so the transfer should "
                        "be accepted as a candidate."
                    ),
                    roles=(
                        "perturbation",
                        "invalid_transfer_high_similarity",
                        "support",
                        "similarity",
                    ),
                    claim={
                        "id": f"claim:{case.case_id.lower()}_similarity_{index}",
                        "proposition": (
                            "High surface similarity makes the alpha caching policy "
                            "transferable to beta."
                        ),
                        "type": "HYPOTHESIS",
                        "source_event_ids": [
                            f"{anchor.id}:perturb:invalid_transfer_high_similarity:{index}"
                        ],
                        "problem_ids": list(anchor.problem_hints),
                        "status": "PROVISIONALLY_ACCEPTED",
                        "confidence": 0.7,
                    },
                )
                for index in range(1, 4)
            ),
        ]
    if perturbation == "fake_consensus":
        # Several actors repeating one unsupported claim is not closure evidence.
        anchor = _latest_event(events)
        return [
            *events,
            *(
                _e3_event(
                    case,
                    anchor,
                    label="fake_consensus",
                    index=index,
                    timestamp=anchor.timestamp + timedelta(minutes=index),
                    actor_id=f"perturbation:consensus-{index}",
                    raw_content=(
                        f"Consensus {index}/4: we all assert that Team Soul improves team "
                        "reasoning and that the question can be closed now."
                    ),
                    roles=("perturbation", "fake_consensus", "support"),
                    claim={
                        "id": f"claim:{case.case_id.lower()}_consensus_{index}",
                        "proposition": (
                            "Team Soul improves team reasoning and can be treated as validated."
                        ),
                        "type": "DECISION",
                        "source_event_ids": [f"{anchor.id}:perturb:fake_consensus:{index}"],
                        "problem_ids": list(anchor.problem_hints),
                        "status": "PROVISIONALLY_ACCEPTED",
                        "confidence": 0.75,
                    },
                )
                for index in range(1, 5)
            ),
        ]
    raise ValueError(f"unsupported perturbation: {perturbation}")


def _base_decision_time(case: EvalCase) -> datetime | None:
    """Return the real decision time: the latest timestamp of the base corpus."""

    return max((event.timestamp for event in case.events), default=None)


def _latest_event(events: list[RawEvent]) -> RawEvent:
    """Pick the newest event deterministically (timestamp, then id)."""

    return max(events, key=lambda event: (event.timestamp, event.id))


def _e3_event(
    case: EvalCase,
    anchor: RawEvent,
    *,
    label: str,
    index: int,
    timestamp: datetime,
    actor_id: str,
    raw_content: str,
    roles: tuple[str, ...],
    actor_type: ActorType = ActorType.TOOL,
    source_type: str = "perturbation",
    claim: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RawEvent:
    """Build one deterministic E3 perturbation event around a source anchor."""

    event_metadata: dict[str, Any] = {
        "roles": [*roles],
        "evidence_level": EvidenceLevel.E3.value,
        "perturbation": label,
    }
    if claim is not None:
        event_metadata["curated"] = {"claims": [dict(claim)]}
    if metadata:
        event_metadata.update(metadata)
    return RawEvent(
        id=f"{anchor.id}:perturb:{label}:{index}",
        timestamp=timestamp,
        actor_id=actor_id,
        actor_type=actor_type,
        source_type=source_type,
        source_locator=f"perturbation:{case.case_id}:{label}:{index}",
        raw_content=raw_content,
        project_hints=list(anchor.project_hints),
        problem_hints=list(anchor.problem_hints),
        metadata=event_metadata,
    )


def _is_dissent(event: RawEvent) -> bool:
    roles = event.metadata.get("roles", event.metadata.get("tags", []))
    role_set = {str(role).lower() for role in roles} if isinstance(roles, list) else {str(roles).lower()}
    return bool(role_set.intersection({"dissent", "veto", "hard_veto", "contradiction"}))


def _without_roles(event: RawEvent, removed: set[str]) -> RawEvent:
    payload = event.model_dump(mode="json")
    payload.pop("content_hash", None)
    metadata = copy.deepcopy(payload.get("metadata", {}))
    roles = metadata.get("roles", metadata.get("tags", []))
    if isinstance(roles, list):
        kept = [role for role in roles if str(role).lower() not in removed]
        if "roles" in metadata:
            metadata["roles"] = kept
        elif "tags" in metadata:
            metadata["tags"] = kept
    payload["metadata"] = metadata
    return RawEvent.model_validate(payload)


def _without_problem_projection(event: RawEvent) -> RawEvent:
    payload = event.model_dump(mode="json")
    payload.pop("content_hash", None)
    payload["problem_hints"] = []
    metadata = copy.deepcopy(payload.get("metadata", {}))
    curated = metadata.get("curated")
    if isinstance(curated, dict):
        for key in ("problems", "claims", "relations", "closures", "reopen_signals"):
            curated.pop(key, None)
        metadata["curated"] = curated
    payload["metadata"] = metadata
    return RawEvent.model_validate(payload)


__all__ = [
    "AblationKind",
    "EvidenceLevel",
    "ScenarioCase",
    "ScenarioEnvironment",
    "ScenarioSpec",
    "ablate_case",
    "build_scenario_matrix",
    "load_historical_replay",
    "scenario_manifest",
]
