from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from .contracts import Contract
from .hashing import sha256_json
from .retrieval import estimate_tokens


class ReasoningMode(StrEnum):
    DIRECT = "DIRECT"
    STRUCTURED_TRACE = "STRUCTURED_TRACE"


class ReasoningTrace(Contract):
    """Auditable reasoning scaffold; this contract deliberately excludes private CoT."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)

    trace_id: str
    problem_ids: list[str] = Field(default_factory=list)
    claims_considered: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    transfer_candidates: list[str] = Field(default_factory=list)
    rejected_alternatives: list[dict[str, Any]] = Field(default_factory=list)
    closure_preconditions: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    proposed_action: str | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    token_cost: int | None = None
    latency_ms: int | None = None
    trace_hash: str = ""

    @model_validator(mode="after")
    def ensure_trace_hash(self) -> "ReasoningTrace":
        payload = self.model_dump(mode="json", exclude={"trace_hash"})
        computed = sha256_json(payload)
        if not self.trace_hash:
            object.__setattr__(self, "trace_hash", computed)
        elif self.trace_hash != computed:
            raise ValueError("trace_hash does not match structured reasoning fields")
        return self


def build_reasoning_trace(result: dict[str, Any], arm: str) -> ReasoningTrace:
    """Build a trace from externally visible evaluation facts, never hidden model CoT."""

    arm_result = result.get("arms", {}).get(arm, {})
    retrieval = arm_result.get("retrieval", {})
    selected = retrieval.get("selected", [])
    evidence_refs = [str(item.get("event_id")) for item in selected if item.get("event_id")]
    conflicts = [
        str(item.get("event_id"))
        for item in selected
        if str(item.get("evidence_kind", "")).lower() in {"contradict", "dissent", "veto"}
    ]
    transfer = result.get("transfer") or {}
    transfer_candidates = []
    if transfer:
        transfer_candidates.append(
            f"{transfer.get('source_case_id', '')}->{transfer.get('target_case_id', '')}:"
            f"{transfer.get('decision', 'UNVERIFIED')}"
        )
    closure = result.get("closure") or {}
    closure_reasons = [str(item) for item in closure.get("reason_codes", [])]
    closure_preconditions = [
        *closure_reasons,
        *[
            str(item.get("key"))
            for item in closure.get("reopen_conditions", [])
            if item.get("key")
        ],
    ]
    unresolved = [
        str(item.get("source_event_id"))
        for item in closure.get("unresolved_refs", [])
        if item.get("source_event_id")
    ]
    metrics = arm_result.get("metrics", {})
    claims = [
        f"arm={arm}",
        f"provenance_coverage={metrics.get('provenance_coverage', 0.0)}",
        f"dissent_preservation_rate={metrics.get('dissent_preservation_rate', 0.0)}",
    ]
    rejected: list[dict[str, Any]] = []
    if closure.get("status") in {"NO_CLOSURE", "REVOKED", "REOPENED"}:
        rejected.append(
            {
                "candidate": "operational closure",
                "decision": str(closure.get("status")),
                "reason_codes": closure_reasons,
            }
        )
    if transfer.get("decision") in {"REJECT", "HOLD", "UNVERIFIED"}:
        rejected.append(
            {
                "candidate": "cross-scope transfer",
                "decision": str(transfer.get("decision")),
                "reason_codes": [str(item) for item in transfer.get("reason_codes", [])],
            }
        )
    proposed_action = (
        "retain scoped evidence and require human validation before closure"
        if closure.get("status") in {"NO_CLOSURE", "REOPENED", "REVOKED"}
        else "continue scoped evaluation with explicit provenance"
    )
    verification = {
        "projection_rebuild_stable": result.get("projection_stable", False),
        "context_hash": arm_result.get("context", {}).get("context_hash", ""),
        "provider_citation_coverage": metrics.get("provider_citation_coverage", 0.0),
    }
    problem_ids = [str(closure["problem_id"])] if closure.get("problem_id") else []
    trace_id = "trace:" + sha256_json(
        {
            "case_id": result.get("case_id", ""),
            "arm": arm,
            "evidence_used": evidence_refs,
        }
    )[:20]
    trace_tokens = estimate_tokens(" ".join(claims + evidence_refs + conflicts))
    return ReasoningTrace(
        trace_id=trace_id,
        problem_ids=problem_ids,
        claims_considered=claims,
        evidence_used=evidence_refs,
        conflicting_evidence=conflicts,
        transfer_candidates=transfer_candidates,
        rejected_alternatives=rejected,
        closure_preconditions=closure_preconditions,
        unresolved_items=unresolved,
        proposed_action=proposed_action,
        verification=verification,
        token_cost=trace_tokens,
        latency_ms=None,
    )


def private_cot_fields() -> tuple[str, ...]:
    forbidden = ("cot", "chain", "thought", "hidden_reasoning", "scratchpad")
    return tuple(
        name
        for name in ReasoningTrace.model_fields
        if any(token in name.lower() for token in forbidden)
    )


# --- G2: the two scaffolds and their prompt-level instruction ---------------------
#
# DIRECT is the unchanged baseline: ``scaffold_contract`` returns the base contract
# byte-for-byte so no earlier context hash moves. STRUCTURED_TRACE appends a declared
# sequence of auditable labels. PLAN_RETRIEVE_CHECK_ANSWER is deliberately absent: the
# owner froze G2 to DIRECT versus STRUCTURED_TRACE only.

STRUCTURED_TRACE_INSTRUCTION = (
    "Answer through this auditable sequence, reusing these exact labels on their own lines:\n"
    "PROBLEM: restate the problem in one sentence.\n"
    "EVIDENCE_CONSIDERED: the bracketed event ids you actually relied on.\n"
    "COUNTEREVIDENCE: bracketed event ids that argue against your answer, or NONE.\n"
    "DISSENT: bracketed event ids recording objections or vetoes, or NONE.\n"
    "TRANSFER_CHECK: whether the case transfers, and the specific condition that decides it.\n"
    "REJECTED_ALTERNATIVES: each alternative you rejected with a one-line reason.\n"
    "UNCERTAINTY: what remains unknown or unverifiable.\n"
    "PROPOSED_ACTION: the action you recommend, or NONE.\n"
    "VERIFICATION: how you checked the answer against the evidence.\n"
    "ANSWER: your final answer in at most four sentences.\n"
    "Do not describe hidden deliberation; report only what the evidence supports."
)

REQUIRED_TRACE_LABELS = (
    "PROBLEM",
    "EVIDENCE_CONSIDERED",
    "COUNTEREVIDENCE",
    "DISSENT",
    "TRANSFER_CHECK",
    "REJECTED_ALTERNATIVES",
    "UNCERTAINTY",
    "PROPOSED_ACTION",
    "VERIFICATION",
    "ANSWER",
)

_TRACE_LABEL_RE = re.compile(r"^\s*([A-Z_]{3,24})\s*:\s*(.*)$")
# Epoch 4: this corpus uses ids containing ':' (e.g. 'evt_c05_2:perturb:conflict'). The previous
# alphabet stopped at the colon and extracted a truncated id that was never sent, which made the new
# unsupported-reference check report a false violation on 3 real rows. Widened to the corpus
# alphabet; trailing separators are stripped so sentence punctuation is not captured.
_EVENT_ID_RE = re.compile(r"\b(evt_[A-Za-z0-9_:.\-]+)")


def scaffold_contract(base_contract: str, scaffold: ReasoningMode | str) -> str:
    """Return the system contract for a scaffold; DIRECT returns the base unchanged."""

    value = ReasoningMode(scaffold)
    if value is ReasoningMode.DIRECT:
        return base_contract
    return base_contract + "\n\n" + STRUCTURED_TRACE_INSTRUCTION


def parse_trace_sections(answer: str) -> dict[str, str]:
    """Split an answer into the labels the contract asked for.

    This reads labels a contract requested; it never mines free-form deliberation.
    """

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in answer.splitlines():
        match = _TRACE_LABEL_RE.match(line)
        label = match.group(1) if match else None
        if label in REQUIRED_TRACE_LABELS:
            current = label
            sections.setdefault(current, []).append(match.group(2).strip())
            continue
        if current is not None and line.strip():
            sections[current].append(line.strip())
    return {name: " ".join(parts).strip() for name, parts in sections.items()}


def trace_label_completeness(answer: str) -> float:
    """Fraction of the requested labels the answer actually used."""

    sections = parse_trace_sections(answer)
    if not sections:
        return 0.0
    present = sum(1 for label in REQUIRED_TRACE_LABELS if sections.get(label))
    return present / len(REQUIRED_TRACE_LABELS)


def scaffold_utility(
    *,
    metrics: dict[str, float],
    answer: str,
    selected_event_ids: list[str],
    dissent_event_ids: list[str],
    counterevidence_event_ids: list[str],
    tokens_used: int,
    tokens_budget: int,
    latency_ms: int,
    latency_budget_ms: int = 60000,
) -> dict[str, Any]:
    """Declared reasoning-utility heuristic: a vector plus one indicative scalar.

    This is an engineering heuristic, not a validated utility function. It is never the
    sole basis of a promotion decision (Prompt chapter VIII); every component is reported
    beside it and the vector is authoritative.
    """

    selected = set(selected_event_ids)
    cited = set(cited_event_ids(answer)) & selected
    if not selected:
        return {
            "components": {},
            "unscored_components": ["citation_rate", "counterevidence_surfaced", "dissent_surfaced"],
            "benefit": None,
            "scalar": None,
            "status": "UNSCORED: no evidence was selected, so no utility can be computed",
        }
    components = {
        "citation_rate": (len(cited) / len(selected)) if selected else 0.0,
        "counterevidence_surfaced": 1.0 if (set(counterevidence_event_ids) & cited) else 0.0,
        "dissent_surfaced": 1.0 if (set(dissent_event_ids) & cited) else 0.0,
        "trace_label_completeness": trace_label_completeness(answer),
    }
    # Epoch 4 (F-2): a metric that was never scored must not be folded in as a win. It is
    # dropped from the mean and named, rather than silently contributing 1.0.
    unscored_components: list[str] = []
    for name, key in (
        ("no_unsupported_claim", "unsupported_claim_rate"),
        ("no_false_closure", "false_closure_rate"),
        ("no_false_no_closure", "false_no_closure_rate"),
    ):
        raw = metrics.get(key)
        if isinstance(raw, int | float):
            components[name] = 1.0 - float(raw)
        else:
            unscored_components.append(name)
    benefit = sum(components.values()) / len(components)
    token_ratio = (tokens_used / tokens_budget) if tokens_budget else 0.0
    latency_ratio = (latency_ms / latency_budget_ms) if latency_budget_ms else 0.0
    return {
        "components": {name: round(value, 6) for name, value in components.items()},
        "unscored_components": unscored_components,
        "benefit": round(benefit, 6),
        "token_ratio": round(token_ratio, 6),
        "latency_ratio": round(latency_ratio, 6),
        "scalar": round(benefit / (1.0 + token_ratio + latency_ratio), 6),
        "formula": "mean(components) / (1 + token_ratio + latency_ratio)",
        "caveat": "declared heuristic; the vector is authoritative, the scalar is indicative only",
    }


def cited_event_ids(answer: str) -> list[str]:
    """Event ids the answer names, sorted and de-duplicated.

    Epoch 4: trailing separators are stripped so that sentence punctuation ("...evt_ghost.") and a
    following colon are not captured as part of the id. This is what keeps the extractor's alphabet
    aligned with the corpus, whose ids legitimately contain colons.
    """

    found = (_match.rstrip(".:-") for _match in _EVENT_ID_RE.findall(answer))
    return sorted({item for item in found if item})


__all__ = [
    "ReasoningMode",
    "ReasoningTrace",
    "REQUIRED_TRACE_LABELS",
    "STRUCTURED_TRACE_INSTRUCTION",
    "build_reasoning_trace",
    "cited_event_ids",
    "parse_trace_sections",
    "private_cot_fields",
    "scaffold_contract",
    "scaffold_utility",
    "trace_label_completeness",
]
