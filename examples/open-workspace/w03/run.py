"""Start the Week03 Streamlit slice with the loopback/telemetry-safe launcher."""

from __future__ import annotations

import subprocess

from .launcher import GUI, build_command


def main() -> int:
    return subprocess.call(build_command(), cwd=str(GUI))


if __name__ == "__main__":
    raise SystemExit(main())
