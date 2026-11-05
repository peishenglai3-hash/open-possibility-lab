from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
from typing import Any
from uuid import uuid4

from ..hashing import canonical_json, sha256_json, sha256_text
from .contracts import SandboxReplayReceipt, SandboxReplayResult


# The evaluator signer is process-private. Candidate code receives only the public
# result fields and cannot manufacture a valid signature through the public contract.
# When an evaluator identity must survive a process boundary it is supplied out of
# band through TEAM_SOUL_EVALUATOR_KEY; the key is never written to the store or to
# any other candidate-readable artifact (FR-03 correction).
_EVALUATOR_SIGNING_KEY = secrets.token_bytes(32)
_EVALUATOR_KEY_ENV = "TEAM_SOUL_EVALUATOR_KEY"
EVALUATOR_VERSION = "team-soul-evaluator.v02r"


def issue_sandbox_receipt(
    result: SandboxReplayResult,
    *,
    candidate_policy_id: str,
    candidate_policy_version: str,
    code_hash: str,
    suite_hash: str,
    corpus_hash: str,
    split_hash: str,
    config_hash: str,
    evaluator_hash: str,
    run_id: str | None = None,
    issued_at: datetime | None = None,
    transition_id: str = "",
    base_policy_version: str = "",
    effective_time: str = "",
    store=None,
) -> SandboxReplayReceipt:
    issued = issued_at or datetime.now(timezone.utc)
    run = run_id or f"sandbox:{candidate_policy_id}:{uuid4().hex}"
    receipt_id = f"receipt:{uuid4().hex}"
    payload: dict[str, Any] = {
        "receipt_id": receipt_id,
        "candidate_policy_id": candidate_policy_id,
        "candidate_policy_version": candidate_policy_version,
        "code_hash": code_hash,
        "suite_hash": suite_hash,
        "corpus_hash": corpus_hash,
        "split_hash": split_hash,
        "config_hash": config_hash,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": evaluator_hash,
        "run_id": run,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "result_hash": _result_hash(result),
    }
    # G0-C: optional bindings are added only when supplied, so receipts issued before the
    # binding existed keep their exact payload and signature.
    if transition_id:
        payload["transition_id"] = transition_id
    if base_policy_version:
        payload["base_policy_version"] = base_policy_version
    if effective_time:
        payload["effective_time"] = effective_time
    signature = hmac.new(
        _signing_key(store),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return SandboxReplayReceipt(**payload, signature=signature)


def verify_sandbox_receipt(
    result: SandboxReplayResult,
    receipt: SandboxReplayReceipt | None,
    *,
    candidate_policy_id: str,
    candidate_policy_version: str,
    transition_id: str = "",
    base_policy_version: str = "",
    effective_time: str = "",
    store=None,
) -> bool:
    if receipt is None:
        return False
    # G0-C: a binding carried by the receipt must match the transition consuming it.
    if getattr(receipt, "transition_id", "") and receipt.transition_id != transition_id:
        return False
    if getattr(receipt, "base_policy_version", "") and (
        receipt.base_policy_version != base_policy_version
    ):
        return False
    if getattr(receipt, "effective_time", "") and receipt.effective_time != effective_time:
        return False
    if receipt.candidate_policy_id != candidate_policy_id:
        return False
    if receipt.candidate_policy_version != candidate_policy_version:
        return False
    if receipt.evaluator_version != EVALUATOR_VERSION:
        return False
    if receipt.result_hash != _result_hash(result):
        return False
    payload = receipt.model_dump(mode="json", exclude={"signature"})
    # G0-C: empty optional bindings were absent from the signed payload, so they must not
    # reappear when the signature is recomputed (otherwise pre-binding receipts break).
    for key in ("transition_id", "base_policy_version", "effective_time"):
        if not payload.get(key):
            payload.pop(key, None)
    expected = hmac.new(
        _signing_key(store),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(receipt.signature, expected)


def replay_result_hash(result: SandboxReplayResult) -> str:
    """Expose the bound result hash for audit manifests without the signer key."""

    return _result_hash(result)


def _result_hash(result: SandboxReplayResult) -> str:
    return sha256_json(result.model_dump(mode="json", exclude={"receipt"}))


def evaluator_code_hash() -> str:
    return sha256_text("team_soul.kernel.receipts:" + EVALUATOR_VERSION)


def _signing_key(store=None) -> bytes:
    """Resolve the evaluator signing key from evaluator-owned state only.

    The key is never persisted in the store, and therefore never readable by
    candidate code through a store handle. Resolution order:

    1. TEAM_SOUL_EVALUATOR_KEY (hex, 32 bytes) - explicit evaluator identity that
       can survive a process boundary without touching candidate-visible state;
    2. otherwise a process-local key, so receipts are verifiable within the
       evaluator process that issued them.

    The store argument is retained for call-site compatibility only and is never
    used as key material.
    """

    override = os.environ.get(_EVALUATOR_KEY_ENV)
    if override:
        try:
            key = bytes.fromhex(override.strip())
        except ValueError as exc:
            raise ValueError(f"{_EVALUATOR_KEY_ENV} must be hex-encoded") from exc
        if len(key) != 32:
            raise ValueError(f"{_EVALUATOR_KEY_ENV} must encode exactly 32 bytes")
        return key
    return _EVALUATOR_SIGNING_KEY


__all__ = [
    "EVALUATOR_VERSION",
    "evaluator_code_hash",
    "issue_sandbox_receipt",
    "replay_result_hash",
    "verify_sandbox_receipt",
]
