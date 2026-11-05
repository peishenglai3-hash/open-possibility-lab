"""FR-06 regression: provider redaction must fail closed on serialized shapes and stay usable.

Reported by the fresh adversarial review (A9/A10) and the fresh conformance review (Finding F):
  - escaped-quote JSON such as curl -d "{\"api_key\": \"CANARY\"}" was SENT with the secret intact;
    the C2 required-evidence list explicitly named escaped JSON, yet no test covered it;
  - near-miss key names (secret_key / passwd / credentials) were neither redacted nor detected;
  - symmetrically, a correctly redacted payload was blocked because canonical_json re-escapes the
    redaction marker and the residual scan then failed to recognise its own output.

Invariants under test: nothing secret-shaped crosses the remote provider boundary, and the boundary
must not refuse the content it has itself sanitised.
"""

from __future__ import annotations

import pytest

from team_soul.hashing import canonical_json
from team_soul.privacy import redact_for_remote, redact_payload_for_remote


def test_escaped_json_credential_is_redacted() -> None:
    text = 'curl -d "{\\"api_key\\": \\"CANARY-SECRET-9f3a\\"}"'
    redacted = redact_for_remote(text)
    assert "CANARY-SECRET-9f3a" not in redacted


@pytest.mark.parametrize("shape", ["secret_key=ABC123", "passwd=hunter2", "credentials=xyz789"])
def test_near_miss_credential_keys_are_redacted(shape: str) -> None:
    redacted = redact_for_remote(shape)
    assert "ABC123" not in redacted
    assert "hunter2" not in redacted
    assert "xyz789" not in redacted


def test_redacted_payload_is_not_blocked_by_its_own_marker() -> None:
    result = redact_payload_for_remote({"note": "raw note with api_key=CANARY"})
    assert "CANARY" not in canonical_json(result)
    assert "REDACTED" in canonical_json(result)


def test_nested_serialized_secret_is_redacted_or_refused() -> None:
    payload = {"nested": {"items": ['{"password": "hunter2"}']}}
    result = redact_payload_for_remote(payload)
    assert "hunter2" not in canonical_json(result)
