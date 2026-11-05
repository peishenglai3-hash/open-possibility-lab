"""Observed workflow addendum: actual AppTest clicks for BASELINE/OPTIONAL/FORCED.

Post-hoc exploratory evidence, not part of the frozen G3 preregistration. It measures what the UI
actually requires; it is not used to tune the design. No model/network/authority.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
APP = HERE / "gui-or-slice" / "streamlit_app.py"


def run_mode(mode: str) -> dict:
    from streamlit.testing.v1 import AppTest

    if mode == "FORCED":
        os.environ["W03_FORCED"] = "1"
    else:
        os.environ.pop("W03_FORCED", None)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    clicks = 0
    panels = 0
    if mode == "FORCED":
        for name in ("sources", "dissent", "alternatives", "unresolved", "revision", "untrusted", "excluded"):
            at.button(key="expand_" + name).click().run()
            clicks += 1
            panels += 1
    at.button(key="direct_answer").click().run()
    clicks += 1
    answer_text = " ".join(item.value for item in at.markdown)
    captions = " ".join(item.value for item in at.caption)
    match = re.search(r"answer_sha256: ([0-9a-f]+)", captions)
    try:
        at.button(key="stop")
        stop_available = True
    except KeyError:
        stop_available = False
    result = {
        "mode": mode,
        "clicks_to_answer": clicks,
        "panels_expanded_before_answer": panels,
        "answer_sha256": match.group(1) if match else "",
        "answer_present": "Adopt with conditions" in answer_text or "UNKNOWN / HOLD" in answer_text or "premise is not supported" in answer_text,
        "export_buttons": len(at.get("download_button")),
        "stop_available": stop_available,
        "exception_count": len(at.exception),
    }
    os.environ.pop("W03_FORCED", None)
    return result


def main() -> int:
    rows = [run_mode(mode) for mode in ("BASELINE", "OPTIONAL", "FORCED")]
    out = HERE / "experiment" / "observed_workflow.jsonl"
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
