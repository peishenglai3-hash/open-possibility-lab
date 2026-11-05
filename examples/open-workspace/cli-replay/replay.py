"""CLI replay for the Week03 slice: same composer/export, no GUI, no model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT,):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from w03.composer import compose, expansion  # noqa: E402
from w03.export import export_json, export_markdown  # noqa: E402
from w03.project import load_project  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Week03 CLI replay (synthetic, no network)")
    parser.add_argument("--task", default="T1", choices=["T1", "T2", "T3"])
    parser.add_argument("--mode", default="OPTIONAL", choices=["BASELINE", "OPTIONAL", "FORCED"])
    parser.add_argument("--format", default="md", choices=["md", "json"])
    parser.add_argument("--expansion", action="store_true", help="include expansion JSON")
    args = parser.parse_args()
    project = load_project()
    result = compose(project, args.task, args.mode)
    if args.format == "json":
        print(export_json(project.project_id, result))
    else:
        print(export_markdown(project.project_id, result))
    if args.expansion:
        import json

        print(json.dumps(expansion(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
