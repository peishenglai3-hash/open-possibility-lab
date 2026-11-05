"""Export contract for the Week03 slice: allowlisted, portable, no host paths."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .composer import AnswerResult

SCHEMA = "w03-openness-export.v1"
ALLOWED_KEYS = frozenset(
    {
        "schema", "project_id", "task_id", "question", "answer", "status",
        "evidence", "dissent", "alternatives", "unresolved", "revision",
        "limits", "synthetic", "export_sha256",
    }
)
ITEM_KEYS = frozenset({"id", "title", "text", "status"})
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\\\\[^\s]+"),
    re.compile(r"/(?:Users|home|tmp|var)/"),
    re.compile(r"(?i)runtime_dir"),
    re.compile(r"(?i)source_locator"),
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _items(items) -> list[dict[str, str]]:
    return [
        {"id": item.id, "title": item.title, "text": item.text[:240], "status": item.status}
        for item in items
    ]


def build_payload(project_id: str, result: AnswerResult) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "task_id": result.task_id,
        "question": result.question,
        "answer": result.answer,
        "status": result.status,
        "evidence": _items(result.evidence),
        "dissent": _items(result.dissent),
        "alternatives": _items(result.alternatives),
        "unresolved": _items(result.unresolved),
        "revision": _items(result.revision),
        "limits": list(result.limits),
        "synthetic": True,
    }


def assert_allowlisted(payload: dict[str, Any]) -> None:
    extra = sorted(set(payload) - ALLOWED_KEYS)
    if extra:
        raise ValueError("export has non-allowlisted keys: " + ", ".join(extra))
    blob = _canonical(payload)
    for pattern in _PATH_PATTERNS:
        if pattern.search(blob):
            raise ValueError("export contains a host path or locator: " + pattern.pattern)
    for key in ("evidence", "dissent", "alternatives", "unresolved", "revision"):
        for item in payload.get(key, []):
            item_extra = sorted(set(item) - ITEM_KEYS)
            if item_extra:
                raise ValueError("export item has non-allowlisted keys: " + ", ".join(item_extra))


def finalize(payload: dict[str, Any]) -> dict[str, Any]:
    assert_allowlisted(payload)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    final = dict(payload)
    final["export_sha256"] = digest
    assert_allowlisted(final)
    return final


def export_json(project_id: str, result: AnswerResult) -> str:
    return json.dumps(finalize(build_payload(project_id, result)), ensure_ascii=False, indent=2)


def export_markdown(project_id: str, result: AnswerResult) -> str:
    payload = finalize(build_payload(project_id, result))
    lines = [
        "# Week03 synthetic export",
        "",
        "- project: " + payload["project_id"],
        "- task: " + payload["task_id"],
        "- status: " + payload["status"],
        "- synthetic: true (no real team data, no model, no network)",
        "",
        "## Question",
        payload["question"],
        "",
        "## Answer",
        payload["answer"],
        "",
        "## Evidence",
    ]
    for item in payload["evidence"]:
        lines.append(f"- [{item['id']}] {item['title']} ({item['status']}): {item['text']}")
    for section in ("dissent", "alternatives", "unresolved", "revision"):
        lines.extend(["", "## " + section.capitalize()])
        for item in payload[section]:
            lines.append(f"- [{item['id']}] {item['title']} ({item['status']}): {item['text']}")
    lines.extend(["", "## Limits"])
    lines.extend("- " + item for item in payload["limits"])
    lines.extend(["", "## Integrity", "- export_sha256: " + payload["export_sha256"]])
    return "\n".join(lines) + "\n"
