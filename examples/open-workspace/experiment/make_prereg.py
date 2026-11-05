"""Freeze the G3 design and source hashes before the comparison run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiment"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    results = [EXP / "results.jsonl", EXP / "summary.csv"]
    if any(path.exists() for path in results):
        raise SystemExit("results already exist; refusing to re-freeze")
    frozen = [
        ROOT / "project" / "synthetic_project.json",
        ROOT / "w03" / "composer.py",
        ROOT / "w03" / "export.py",
        ROOT / "w03" / "project.py",
        EXP / "design.json",
    ]
    prereg = {
        "experiment": "W03-G3-01",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "hashes": {path.relative_to(ROOT).as_posix(): sha(path) for path in frozen},
        "design": json.loads((EXP / "design.json").read_text(encoding="utf-8")),
    }
    (EXP / "preregistration.json").write_text(
        json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("frozen", len(frozen), "files; experiment", prereg["experiment"])


if __name__ == "__main__":
    main()
