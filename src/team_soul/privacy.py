from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import canonical_json, sha256_text


_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "password",
        "passwd",
        "secret",
        "secret_key",
        "client_secret",
        "credential",
        "credentials",
        "authorization",
        "auth",
        "private_id",
        "private_identifier",
        "private_key",
        "source_locator",
        # Pre-W02 blind review: near-key shapes that the original exact-name list missed.
        "pwd",
        "access_key",
        "secret_access_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "api_token",
        "auth_token",
    }
)
# FR-06: serialized payloads may carry the key/value inside escaped quotes
# (for example curl -d "{\"api_key\": \"CANARY\"}"), so the assignment pattern
# accepts an optional backslash-escaped quote around both the key and the value.
_KEY_NAME = (
    r"aws[_-]?secret[_-]?access[_-]?key|secret[_-]?access[_-]?key|access[_-]?key|"
    r"api[_-]?key|apikey|secret[_-]?key|client[_-]?secret|access[_-]?token|"
    r"api[_-]?token|auth[_-]?token|token|password|passwd|pwd|secret|"
    r"credentials?|authorization|auth|"
    r"private[_-]?(?:id|identifier|key)|source[_-]?locator"
)
_QUOTE = r"(?:\\?[\"'])"
_OPT_QUOTE = _QUOTE + "?"
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?is)(" + _OPT_QUOTE + r"(?:" + _KEY_NAME + r")" + _OPT_QUOTE + r"\s*[:=：]\s*)("
    + _QUOTE + r"(?:\\.|[^\"'\\])*?" + _QUOTE + r"|[^,\s}\]]+)"
)
# Characters stripped when normalising a matched value: backslash, double quote, single quote.
_UNWRAP_CHARS = chr(92) + chr(34) + chr(39)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    _SENSITIVE_ASSIGNMENT,
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bprivate(?:-document)?\s*:\s*[A-Za-z0-9._:-]{8,}"),
)


class PrivacyBoundaryError(RuntimeError):
    """Raised when data cannot be proven safe for a remote provider boundary."""


def _assignment_replacement(match: "re.Match[str]") -> str:
    """Replace a secret value while preserving the payload own quoting style."""

    escaped = chr(92) if match.group(2).startswith(chr(92)) else ""
    return match.group(1) + escaped + chr(34) + "[REDACTED]" + escaped + chr(34)


def _unwrap_marker(value: str) -> str:
    """Normalize a matched value so our own redaction marker is recognised.

    FR-06: canonical_json re-escapes the marker, so the residual scan must strip
    backslashes and quotes before comparing against the marker literals.
    """

    return value.strip().strip(_UNWRAP_CHARS)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern is _SENSITIVE_ASSIGNMENT:
            redacted = pattern.sub(_assignment_replacement, redacted)
        elif pattern.groups:
            if pattern.pattern.startswith("(?i)\\bprivate"):
                redacted = pattern.sub("private:[REDACTED]", redacted)
            else:
                redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def contains_unredacted_secret(text: str) -> bool:
    """Detect secret-shaped material while ignoring our explicit redaction marker."""
    for match in _SENSITIVE_ASSIGNMENT.finditer(text):
        if _unwrap_marker(match.group(2)) not in {"[REDACTED]", "<REDACTED>"}:
            return True
    for pattern in (_SECRET_PATTERNS[0], _SECRET_PATTERNS[2], _SECRET_PATTERNS[3]):
        for match in pattern.finditer(text):
            token = match.group(0).replace("\\", "")
            if "[REDACTED]" not in token and "<REDACTED>" not in token:
                return True
    return False


def redact_for_remote(text: str) -> str:
    """Redact and fail closed if any known secret pattern remains."""
    redacted = redact_secrets(text)
    if contains_unredacted_secret(redacted):
        raise PrivacyBoundaryError("remote provider payload contains an unredacted secret")
    return redacted


def redact_payload_for_remote(payload: Any) -> Any:
    """Recursively redact a provider payload and scan its serialized form."""

    redacted = _redact_value(payload)
    serialized = canonical_json(redacted)
    if contains_unredacted_secret(serialized):
        raise PrivacyBoundaryError("remote provider payload contains an unredacted secret")
    return redacted


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if normalized in _SENSITIVE_KEY_NAMES:
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_for_remote(value)
    return value


@dataclass(frozen=True)
class PrivateCorpusMapping:
    """Non-content mapping for private ingestion/evaluation.

    The mapping intentionally exposes only stable opaque identifiers. Raw
    private files stay outside the repository and are never copied into public
    fixtures by this class.
    """

    root: Path

    def locator(self, source: str | Path) -> str:
        path = Path(source).resolve()
        try:
            relative = path.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            relative = path.name
        return f"private:{sha256_text(relative)[:20]}"

    def actor_id(self, source: str | Path) -> str:
        return f"private-document:{sha256_text(str(Path(source).resolve()))[:20]}"
