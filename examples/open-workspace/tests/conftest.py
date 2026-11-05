"""Test path setup for the Week03 slice; no network or authority imports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "gui-or-slice"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
