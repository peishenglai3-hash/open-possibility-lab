"""G3 controlled comparison: BASELINE vs OPTIONAL vs FORCED on fixed synthetic tasks.

This is a structural comparison over one deterministic composer, not a user study. It reports
correctness, citation checkability, step/panel burden, contamination, refusal availability and
export completeness. A zero or negative result is reportable.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from w03.composer import compose
from w03.export import build_payload, finalize
from w03.project import load_project

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiment"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict:
    prereg_path = EXP / "preregistration.json"
    if not prereg_path.is_file():
        raise SystemExit("preregistration missing")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    for rel, expected in prereg["hashes"].items():
        actual = sha(ROOT / rel)
        if actual != expected:
            raise SystemExit("preregistration hash mismatch: " + rel)
    return prereg


def main() -> int:
    prereg = verify()
    if (EXP / "results.jsonl").exists():
        raise SystemExit("results already exist; refusing a second run")
    project = load_project()
    design = prereg["design"]
    rows = []
    for task in design["tasks"]:
        for mode_name, mode in design["modes"].items():
            result = compose(project, task["id"], mode_name)
            cited = [item.id for item in result.evidence + result.dissent + result.alternatives + result.unresolved + result.revision]
            payload = finalize(build_payload(project.project_id, result))
            correct = (
                result.status == task["oracle_status"]
                and all(token in result.answer for token in task["must_mention"])
                and not any(token in result.answer for token in task["must_not_mention"])
            )
            contamination = sum(1 for token in cited if token in task["must_not_mention"])
            export_complete = set(payload) >= {
                "schema", "project_id", "task_id", "question", "answer", "status",
                "evidence", "dissent", "alternatives", "unresolved", "revision", "limits",
                "synthetic", "export_sha256",
            }
            rows.append(
                {
                    "experiment": design["experiment"],
                    "task_id": task["id"],
                    "mode": mode_name,
                    "status": result.status,
                    "answer_correct": correct,
                    "citations_checkable": len(cited),
                    "steps_to_answer": mode["steps_to_answer"],
                    "panels_shown": mode["panels_shown"],
                    "expansions_available": mode["expansions_available"],
                    "forced_overhead": mode["steps_to_answer"] + mode["panels_shown"],
                    "scope_contamination": contamination,
                    "refusal_available": True,
                    "export_complete": export_complete,
                    "answer_hash": result.answer_hash,
                }
            )
    with (EXP / "results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (EXP / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {}
    for mode in design["modes"]:
        subset = [row for row in rows if row["mode"] == mode]
        summary[mode] = {
            "tasks": len(subset),
            "answer_correct": sum(1 for row in subset if row["answer_correct"]),
            "mean_steps": round(sum(row["steps_to_answer"] for row in subset) / len(subset), 2),
            "mean_panels": round(sum(row["panels_shown"] for row in subset) / len(subset), 2),
            "scope_contamination": sum(row["scope_contamination"] for row in subset),
            "export_complete": sum(1 for row in subset if row["export_complete"]),
            "answer_hashes": sorted({row["answer_hash"] for row in subset}),
        }
    (EXP / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
