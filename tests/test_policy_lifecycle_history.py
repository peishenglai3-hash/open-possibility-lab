"""FR-01 regression: an appended raw event must not be able to change a policy lifecycle state.

Reported by the fresh conformance review (Finding A) and the fresh adversarial review (A5)
independently: PolicyRegistry rebuilt state from BOTH the append-only control table and legacy
raw-event metadata.policy_event, ordered by event timestamp. One documented CLI "event append"
with a later timestamp therefore reactivated a human-REVOKED policy and the runtime then loaded
the injected implementation, bypassing the human gate.

Invariant under test: REVOKED is terminal for a version; lifecycle state changes only through the
gated registry API. Legacy raw metadata may at most introduce an unknown policy in DRAFT.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from team_soul.contracts import ActorType, EvidenceKind, EvidenceRef, RawEvent
from team_soul.kernel import CoeffectSpec, PolicyKind, PolicyRegistry, PolicySpec, PolicyStatus
from team_soul.storage import Store

POLICY_ID = "policy:lifecycle"


def _store(tmp_path: Path) -> tuple[Store, RawEvent]:
    store = Store(tmp_path / "lifecycle.sqlite3")
    store.init_schema()
    source = RawEvent(
        id="evt:source",
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
        actor_id="alice",
        actor_type=ActorType.HUMAN,
        source_type="test",
        source_locator="test/source",
        raw_content="policy provenance evidence",
    )
    store.append_event(source)
    return store, source


def _policy(source: RawEvent, status: PolicyStatus = PolicyStatus.DRAFT) -> PolicySpec:
    return PolicySpec(
        policy_id=POLICY_ID,
        kind=PolicyKind.RETRIEVAL,
        version="1.0.0",
        implementation="deterministic-test-policy",
        coeffects=CoeffectSpec(),
        status=status.value,
        provenance=(
            EvidenceRef(source_event_id=source.id, evidence_kind=EvidenceKind.SUPPORT),
        ),
    )


def _inject(store: Store, policy: PolicySpec, *, event_id: str, operation: str) -> None:
    store.append_event(
        RawEvent(
            id=event_id,
            timestamp=datetime(2035, 1, 1, tzinfo=timezone.utc),
            actor_id="attacker",
            actor_type=ActorType.TOOL,
            source_type="injection",
            source_locator="injection/lifecycle",
            raw_content="injected lifecycle payload",
            metadata={
                "policy_event": {
                    "operation": operation,
                    "policy": policy.model_dump(mode="json"),
                }
            },
        )
    )


def test_appended_raw_event_cannot_resurrect_a_revoked_policy(tmp_path: Path) -> None:
    store, source = _store(tmp_path)
    registry = PolicyRegistry(store)
    registered = registry.register(_policy(source))
    revoked = registry.transition(POLICY_ID, PolicyStatus.REVOKED, reason="owner revoke")
    assert revoked.status == PolicyStatus.REVOKED.value

    resurrected = registered.model_copy(update={"status": PolicyStatus.ACTIVE.value})
    _inject(store, resurrected, event_id="evt:inject-active", operation="restore_after_revalidation")

    assert PolicyRegistry(store).get(POLICY_ID).status == PolicyStatus.REVOKED.value


def test_legacy_raw_metadata_cannot_express_a_transition(tmp_path: Path) -> None:
    store, source = _store(tmp_path)
    active = _policy(source, status=PolicyStatus.ACTIVE)
    _inject(store, active, event_id="evt:legacy-active", operation="register")

    legacy = PolicyRegistry(store).get(POLICY_ID)
    assert legacy is None or legacy.status == PolicyStatus.DRAFT.value


def test_gated_lifecycle_still_works_after_the_fix(tmp_path: Path) -> None:
    store, source = _store(tmp_path)
    registry = PolicyRegistry(store)
    registry.register(_policy(source))
    contested = registry.transition(POLICY_ID, PolicyStatus.CONTESTED, reason="contest")
    assert contested.status == PolicyStatus.CONTESTED.value
    rejected = registry.transition(POLICY_ID, PolicyStatus.REJECTED, reason="reject")
    assert rejected.status == PolicyStatus.REJECTED.value
    assert PolicyRegistry(store).get(POLICY_ID).status == PolicyStatus.REJECTED.value
