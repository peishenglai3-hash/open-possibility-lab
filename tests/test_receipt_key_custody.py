"""FR-03 regression: the evaluator receipt signing key must not be readable from
the candidate-accessible store.

Reported by the fresh adversarial review (A2) and the conformance review (Finding C):
the HMAC key was persisted in the same SQLite store that candidate code can read via
the public Store.get_runtime_meta API, so a never-executed SandboxReplayResult could
be hand-signed and accepted as SANDBOXED evidence.

Invariant under test: a trusted evaluation receipt can only be produced by the
evaluator/runtime; candidate-visible state must not contain the signing key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from team_soul.kernel import receipts
from team_soul.storage import Store

KEY_META = "team_soul.evaluator_receipt_signing_key"


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "custody.sqlite3")
    store.init_schema()
    return store


def test_signing_key_is_not_readable_from_store_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = receipts._signing_key(store)
    assert isinstance(key, bytes)
    assert len(key) == 32
    assert store.get_runtime_meta(KEY_META) is None


def test_signing_key_is_absent_from_every_store_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = receipts._signing_key(store)
    store.connection.commit()
    for path in tmp_path.iterdir():
        if not path.is_file():
            continue
        blob = path.read_bytes()
        assert key not in blob, f"signing key material found in {path.name}"
        assert key.hex().encode() not in blob.lower(), f"hex signing key found in {path.name}"


def test_receipt_cannot_be_signed_with_a_key_read_from_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    issued = receipts._signing_key(store)
    attacker_view = store.get_runtime_meta(KEY_META)
    assert attacker_view is None
    forged_key = (attacker_view or "").encode() or b"candidate-guess"
    assert forged_key != issued


def test_evaluator_key_can_be_supplied_out_of_band(monkeypatch, tmp_path: Path) -> None:
    supplied = "ab" * 32
    monkeypatch.setenv("TEAM_SOUL_EVALUATOR_KEY", supplied)
    store = _store(tmp_path)
    assert receipts._signing_key(store).hex() == supplied
    assert store.get_runtime_meta(KEY_META) is None


def test_malformed_evaluator_key_is_rejected(monkeypatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch.setenv("TEAM_SOUL_EVALUATOR_KEY", "not-hex")
    with pytest.raises(ValueError):
        receipts._signing_key(store)
    monkeypatch.setenv("TEAM_SOUL_EVALUATOR_KEY", "abcd")
    with pytest.raises(ValueError):
        receipts._signing_key(store)
