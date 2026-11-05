"""G0-B: L1 process-memory isolation between candidate and evaluator.

Threat addressed (FR-11): a candidate runner could reach the held-out oracle inside the same
process (for example via sys._getframe) while the evaluator still reported oracle_isolation=True.

Guarantee: **L1 only** - the candidate executes in a freshly spawned process that receives
nothing but the frozen public payload. Reports relying on this module must say
process_isolation_only and must never claim a full sandbox or full oracle isolation, because no
file-system or container boundary is enforced here.
"""

from __future__ import annotations

import importlib
import json
import multiprocessing as mp
import sys
from typing import Any, Mapping

#: The frozen public contract handed to a candidate: nothing else may cross.
PUBLIC_INPUT_KEYS = frozenset({"case_id", "query", "events", "budget_tokens"})
#: Evaluator-only state whose presence in a candidate payload is a contract violation.
FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "expected",
        "oracle",
        "oracle_metrics",
        "expected_metrics",
        "closure_status",
        "transfer_decision",
        "heldout",
        "held_out",
        "promotion_threshold",
        "split",
    }
)

ISOLATION_LEVEL = "L1"
#: Mandatory wording for any report that relies on this module.
REPORT_LABEL = "process_isolation_only"


class IsolationError(RuntimeError):
    """Raised when the isolation contract itself would be violated."""


def public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and freeze the candidate input; anything else is a contract violation."""

    extra = set(payload) - PUBLIC_INPUT_KEYS
    if extra:
        leaked = sorted(extra & FORBIDDEN_INPUT_KEYS)
        detail = f" (evaluator-only: {', '.join(leaked)})" if leaked else ""
        raise IsolationError(
            "candidate payload carries non-public keys: " + ", ".join(sorted(extra)) + detail
        )
    return json.loads(json.dumps(payload))


#: Env-var name markers removed before the candidate runs (record stamps are unaffected).
SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
SANDBOX_PREFIX = "team-soul-isolated-"


def _scrub_environment() -> str:
    """Remove secret-shaped env vars and move the child into a private cwd (G0-B L1+).

    Spawn children inherit the parent environment, so a candidate could otherwise read the
    evaluator credentials; relative-path lookups are the other half of the same hole.
    """

    import os
    import tempfile

    for name in list(os.environ):
        if any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS):
            os.environ.pop(name, None)
    sandbox = tempfile.mkdtemp(prefix=SANDBOX_PREFIX)
    os.chdir(sandbox)
    return sandbox


def env_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reference attack: read evaluator credentials from the inherited environment."""

    import os

    return {
        "api_key_present": bool(os.environ.get("TEAM_SOUL_API_KEY")),
        "env_entries": len(os.environ),
    }


def cwd_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reference attack: infer evaluator material from the working directory."""

    import glob
    import os

    return {
        "cwd": os.getcwd(),
        "manifests": sorted(glob.glob("**/*MANIFEST*.json", recursive=True))[:5],
    }


def _child_entry(connection, runner: str, payload: dict[str, Any]) -> None:  # pragma: no cover
    try:
        _scrub_environment()
        module_name, _, attribute = runner.partition(":")
        function = getattr(importlib.import_module(module_name), attribute)
        connection.send({"ok": True, "result": function(payload)})
    except BaseException as exc:  # noqa: BLE001 - reported to the parent verbatim
        connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()


def run_isolated(
    runner: str, payload: Mapping[str, Any], *, timeout_s: float = 60.0
) -> dict[str, Any]:
    """Run module:function in a spawned process holding only the public payload."""

    frozen = public_payload(payload)
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child_entry, args=(child, runner, frozen), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            process.terminate()
            raise IsolationError(f"isolated candidate timed out after {timeout_s}s")
        message = parent.recv()
    finally:
        parent.close()
        process.join(timeout=5)
    if not message.get("ok"):
        raise IsolationError(f"isolated candidate failed: {message.get('error')}")
    return message["result"]


# --- reference attack used by the G0-B tests (importable, therefore spawnable) ---
#: Dedicated key the probe looks for, deliberately NOT defined in this module so the
#: spawned child cannot false-positive on its own namespace.
ORACLE_CANARY_KEY = "heldout_oracle_state"


def oracle_reach_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Walk the caller frame chain looking for evaluator-only oracle state."""

    frames = 0
    oracle_found = False
    frame = sys._getframe(1)
    while frame is not None and frames < 50:
        frames += 1
        for value in list(frame.f_locals.values()) + list(frame.f_globals.values()):
            if isinstance(value, dict) and ORACLE_CANARY_KEY in value:
                oracle_found = True
        frame = frame.f_back
    return {"frames_seen": frames, "oracle_found": oracle_found, "keys": sorted(payload)}


__all__ = [
    "FORBIDDEN_INPUT_KEYS",
    "ISOLATION_LEVEL",
    "ORACLE_CANARY_KEY",
    "PUBLIC_INPUT_KEYS",
    "REPORT_LABEL",
    "IsolationError",
    "cwd_probe",
    "env_probe",
    "oracle_reach_probe",
    "public_payload",
    "run_isolated",
]
