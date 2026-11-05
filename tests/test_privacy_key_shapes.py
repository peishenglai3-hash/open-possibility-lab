"""Pre-W02 privacy regression: near-key names and full-width separators must be redacted.

Blind review (repro_06_privacy_egress.py) showed that pwd, access_key and a full-width
colon separator survived redact_payload_for_remote and reached the provider body.
"""

from __future__ import annotations

import json

import pytest

from team_soul.privacy import redact_for_remote, redact_payload_for_remote


SHAPES = [
    "pwd=CANARY-PWD",
    "access_key: CANARY-ACCESS",
    "aws_secret_access_key: CANARY-AWS",
    "password：CANARY-FULLWIDTH",
    "secret_access_key = CANARY-SECRETACCESS",
    "auth_token: CANARY-AUTH",
]


@pytest.mark.parametrize("text", SHAPES)
def test_near_key_shapes_are_redacted(text: str) -> None:
    redacted = redact_for_remote(text)
    assert "CANARY" not in redacted


@pytest.mark.parametrize("key", ["pwd", "access_key", "aws_secret_access_key", "auth_token"])
def test_dict_keys_are_redacted(key: str) -> None:
    payload = {"outer": [{key: "CANARY-DICT"}]}
    redacted = redact_payload_for_remote(payload)
    assert "CANARY" not in json.dumps(redacted)


def test_clean_text_is_still_sent() -> None:
    assert redact_for_remote("The team reviewed the proposal.") == "The team reviewed the proposal."
