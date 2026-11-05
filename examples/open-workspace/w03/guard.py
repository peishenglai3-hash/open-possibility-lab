"""Authority firewall: the Week03 UI must not load or call control-plane modules."""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "team_soul.kernel",
    "team_soul.operators",
    "team_soul.providers",
    "team_soul.eval",
    "team_soul.storage",
    "team_soul.projection",
    "team_soul.scenario",
)


def authority_modules_loaded() -> list[str]:
    return sorted(
        name for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    )


def assert_no_authority_modules() -> None:
    loaded = authority_modules_loaded()
    if loaded:
        raise RuntimeError("authority modules loaded in the interaction slice: " + ", ".join(loaded))


def app_source_has_no_authority_imports(path: str | Path) -> bool:
    text = Path(path).read_text(encoding="utf-8")
    return not any(prefix in text for prefix in FORBIDDEN_PREFIXES)
