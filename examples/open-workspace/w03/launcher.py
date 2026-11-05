"""Launcher contract for the Week03 Streamlit slice (loopback only, no telemetry)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui-or-slice"
APP = GUI / "streamlit_app.py"
CONFIG = GUI / ".streamlit" / "config.toml"
DEFAULT_PORT = 8503


def build_command(port: int = DEFAULT_PORT, force_loopback: bool = True) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--client.showErrorDetails",
        "type",
        "--client.toolbarMode",
        "minimal",
        "--server.enableXsrfProtection",
        "true",
    ]
    if force_loopback:
        command.extend(
            [
                "--server.address",
                "127.0.0.1",
                "--browser.serverAddress",
                "127.0.0.1",
            ]
        )
    return command


def config_text() -> str:
    return CONFIG.read_text(encoding="utf-8")


def assert_effective_config() -> dict[str, str]:
    text = config_text()
    required = {
        "address": 'address = "127.0.0.1"',
        "telemetry": "gatherUsageStats = false",
        "error_details": 'showErrorDetails = "type"',
        "toolbar": 'toolbarMode = "minimal"',
        "xsrf": "enableXsrfProtection = true",
        "allowed_hosts": "localhost",
    }
    missing = [name for name, needle in required.items() if needle not in text]
    if missing:
        raise AssertionError("config missing: " + ", ".join(missing))
    return {name: "ok" for name in required}


def assert_loopback_command(command: list[str]) -> None:
    if "--server.address" not in command:
        raise AssertionError("launcher must pass --server.address")
    index = command.index("--server.address")
    if command[index + 1] != "127.0.0.1":
        raise AssertionError("launcher must bind 127.0.0.1, got " + command[index + 1])
    if "0.0.0.0" in command or "::" in command:
        raise AssertionError("launcher contains a wildcard bind")
