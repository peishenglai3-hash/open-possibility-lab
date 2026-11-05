"""Load the synthetic Week03 project; stdlib only, no runtime/authority imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str
    source: str
    time: str
    scope: str
    status: str
    role: str


@dataclass(frozen=True)
class Task:
    id: str
    label: str
    kind: str


@dataclass(frozen=True)
class Project:
    project_id: str
    title: str
    as_of: str
    limits: tuple[str, ...]
    tasks: dict[str, Task]
    documents: tuple[Document, ...]


def load_project(path: str | Path | None = None) -> Project:
    root = Path(__file__).resolve().parents[1]
    target = Path(path) if path else root / "project" / "synthetic_project.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    documents = tuple(
        Document(
            id=str(item["id"]),
            title=str(item["title"]),
            text=str(item["text"]),
            source=str(item["source"]),
            time=str(item["time"]),
            scope=str(item["scope"]),
            status=str(item["status"]),
            role=str(item["role"]),
        )
        for item in payload["documents"]
    )
    tasks = {
        str(key): Task(id=str(value["id"]), label=str(value["label"]), kind=str(value["kind"]))
        for key, value in payload["tasks"].items()
    }
    return Project(
        project_id=str(payload["project_id"]),
        title=str(payload["title"]),
        as_of=str(payload["as_of"]),
        limits=tuple(str(item) for item in payload["limits"]),
        tasks=tasks,
        documents=documents,
    )
