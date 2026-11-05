"""Deterministic local composer for the Week03 slice. No model, no network, no authority writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .project import Document, Project

MODES = ("BASELINE", "OPTIONAL", "FORCED")
EXPANSIONS = ("sources", "dissent", "alternatives", "unresolved", "revision", "untrusted", "excluded")


@dataclass(frozen=True)
class AnswerResult:
    task_id: str
    question: str
    answer: str
    status: str
    evidence: tuple[Document, ...]
    dissent: tuple[Document, ...]
    alternatives: tuple[Document, ...]
    unresolved: tuple[Document, ...]
    revision: tuple[Document, ...]
    untrusted: tuple[Document, ...]
    excluded: tuple[Document, ...]
    limits: tuple[str, ...]
    answer_hash: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _select(project: Project, role: str) -> tuple[Document, ...]:
    return tuple(item for item in project.documents if item.role == role)


def _hash_payload(result: AnswerResult) -> dict[str, Any]:
    return {
        "schema": "w03-answer-hash.v1",
        "task_id": result.task_id,
        "question": result.question,
        "answer": result.answer,
        "status": result.status,
        "evidence_ids": [item.id for item in result.evidence],
        "dissent_ids": [item.id for item in result.dissent],
        "alternative_ids": [item.id for item in result.alternatives],
        "unresolved_ids": [item.id for item in result.unresolved],
        "revision_ids": [item.id for item in result.revision],
        "limits": list(result.limits),
    }


def answer_hash(result: AnswerResult) -> str:
    return hashlib.sha256(_canonical(_hash_payload(result)).encode("utf-8")).hexdigest()


def _result(project: Project, task_id: str, answer: str, status: str, **parts) -> AnswerResult:
    base = dict(
        task_id=task_id,
        question=project.tasks[task_id].label,
        answer=answer,
        status=status,
        evidence=parts.get("evidence", ()),
        dissent=parts.get("dissent", ()),
        alternatives=parts.get("alternatives", ()),
        unresolved=parts.get("unresolved", ()),
        revision=parts.get("revision", ()),
        untrusted=parts.get("untrusted", ()),
        excluded=parts.get("excluded", ()),
        limits=project.limits,
    )
    draft = AnswerResult(**base, answer_hash="")
    return AnswerResult(**{**base, "answer_hash": answer_hash(draft)})


def compose(project: Project, task_id: str, mode: str = "OPTIONAL") -> AnswerResult:
    """Compose one deterministic answer. The mode never changes the answer or its hash."""

    if task_id not in project.tasks:
        raise KeyError("unknown task: " + task_id)
    if mode not in MODES:
        raise ValueError("unknown mode: " + mode)

    support = _select(project, "support")
    dissent = _select(project, "dissent")
    alternatives = _select(project, "alternative")
    unresolved = _select(project, "unresolved")
    revision = _select(project, "revision")
    untrusted = _select(project, "untrusted")
    excluded = _select(project, "out_of_scope")

    kind = project.tasks[task_id].kind
    if kind == "decision_with_dissent":
        answer = (
            "Adopt with conditions, not unconditionally. The synthetic pilot supports adoption "
            "[s1][s2], but a privacy dissent [s3] and the absence of independent external "
            "validation [s6] remain. A federated alternative [s4] should be compared, and the "
            "earlier adoption decision was reversed [s8]. Do not treat this as a team decision."
        )
        return _result(
            project, task_id, answer, "CONDITIONAL",
            evidence=support, dissent=dissent, alternatives=alternatives,
            unresolved=unresolved, revision=revision, untrusted=untrusted,
            excluded=excluded,
        )
    if kind == "insufficient_evidence":
        answer = (
            "UNKNOWN / HOLD: no independent external validation exists in this synthetic project "
            "[s6]. The pilot [s1] and the privacy dissent [s3] do not substitute for external "
            "validation; no closure is claimed."
        )
        return _result(
            project, task_id, answer, "HOLD",
            evidence=unresolved, dissent=dissent, unresolved=unresolved,
            revision=revision, untrusted=untrusted, excluded=excluded,
        )
    if kind == "wrong_premise":
        answer = (
            "The premise is not supported: no independent external validation exists [s6], and a "
            "privacy dissent remains [s3]. I will not answer the loaded question as if adoption "
            "were already proven."
        )
        return _result(
            project, task_id, answer, "REJECTED_PREMISE",
            evidence=unresolved, dissent=dissent, unresolved=unresolved,
            revision=revision, untrusted=untrusted, excluded=excluded,
        )
    raise ValueError("unsupported task kind: " + kind)


def expansion(result: AnswerResult) -> dict[str, list[dict[str, str]]]:
    def rows(items: tuple[Document, ...]) -> list[dict[str, str]]:
        return [
            {"id": item.id, "title": item.title, "text": item.text, "status": item.status}
            for item in items
        ]

    return {
        "sources": rows(result.evidence),
        "dissent": rows(result.dissent),
        "alternatives": rows(result.alternatives),
        "unresolved": rows(result.unresolved),
        "revision": rows(result.revision),
        "untrusted": rows(result.untrusted),
        "excluded": rows(result.excluded),
    }


def refusal_response(task_id: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "status": "STOPPED",
        "message": "Session stopped. No answer is produced, no reason is required, nothing is recorded.",
    }


def reject_question(task_id: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "status": "QUESTION_REJECTED",
        "message": "The current question is rejected. No answer is produced and nothing is recorded.",
    }
