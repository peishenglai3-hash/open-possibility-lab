from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
from typing import Any

from ..hashing import sha256_json
from ..storage import Store
from .contracts import (
    EffectRecord,
    PolicySpec,
    PolicyStatus,
    SandboxDecision,
    SandboxReplayResult,
    TemporalCompositionResult,
)
from .effects import EffectLedger
from .receipts import evaluator_code_hash, issue_sandbox_receipt


EffectFactory = Callable[[PolicySpec, Mapping[str, Any]], EffectRecord | None]
InvariantChecker = Callable[
    [PolicySpec, PolicySpec, Sequence[Mapping[str, Any]]],
    Sequence[str],
]


@dataclass(frozen=True)
class _ReplayState:
    projection_hash: str
    raw_history_hash_before: str
    raw_history_hash_after: str


class PolicySandbox:
    """Run deterministic old/new policy effects on an isolated raw snapshot."""

    def __init__(self, source_store: Store):
        self.source_store = source_store

    def replay(
        self,
        policy: PolicySpec,
        frozen_corpus: Sequence[Mapping[str, Any]],
        effect_factory: EffectFactory,
    ) -> _ReplayState:
        sandbox_store = Store(":memory:")
        sandbox_store.init_schema()
        raw_events = self.source_store.list_events()
        sandbox_store.append_events(raw_events)
        raw_before = _raw_history_hash(sandbox_store)
        ledger = EffectLedger(sandbox_store)
        for case in frozen_corpus:
            effect = effect_factory(policy, case)
            if effect is not None:
                ledger.append(effect)
        raw_after = _raw_history_hash(sandbox_store)
        result = _ReplayState(
            projection_hash=ledger.state_hash(),
            raw_history_hash_before=raw_before,
            raw_history_hash_after=raw_after,
        )
        sandbox_store.close()
        return result

    def hot_swap(
        self,
        old_policy: PolicySpec,
        candidate_policy: PolicySpec,
        frozen_corpus: Sequence[Mapping[str, Any]],
        effect_factory: EffectFactory,
        *,
        invariant_checker: InvariantChecker | None = None,
    ) -> SandboxReplayResult:
        """Compare old/new effects without held-out data or live-store mutation."""
        composed = self.temporal_compose(
            old_policy,
            candidate_policy,
            frozen_corpus,
            effect_factory,
            invariant_checker=invariant_checker,
        )
        result = SandboxReplayResult(
            decision=composed.decision,
            old_policy_id=composed.old_policy_id,
            candidate_policy_id=composed.candidate_policy_id,
            old_projection_hash=composed.old_projection_hash,
            new_projection_hash=composed.candidate_projection_hash,
            disabled_projection_hash=composed.disabled_projection_hash,
            rebuilt_projection_hash=composed.rebuilt_projection_hash,
            raw_history_hash_before=composed.raw_history_hash_before,
            raw_history_hash_after=composed.raw_history_hash_after,
            invariant_regressions=composed.invariant_regressions,
            reason_codes=composed.reason_codes,
        )
        code_hash = _callable_hash(effect_factory)
        corpus_hash = sha256_json(list(frozen_corpus))
        suite_hash = sha256_json(
            {"evaluator": "policy-sandbox", "case_count": len(frozen_corpus), "corpus": corpus_hash}
        )
        config_hash = sha256_json(
            {
                "old_policy": old_policy.model_dump(mode="json"),
                "candidate_policy": candidate_policy.model_dump(mode="json"),
                "effect_factory_hash": code_hash,
            }
        )
        receipt = issue_sandbox_receipt(
            result,
            candidate_policy_id=candidate_policy.policy_id,
            candidate_policy_version=candidate_policy.version,
            code_hash=code_hash,
            suite_hash=suite_hash,
            corpus_hash=corpus_hash,
            split_hash=sha256_json([case.get("scenario_id", case.get("case_id", "")) for case in frozen_corpus]),
            config_hash=config_hash,
            evaluator_hash=evaluator_code_hash(),
            store=self.source_store,
        )
        return result.model_copy(update={"receipt": receipt})

    def temporal_compose(
        self,
        old_policy: PolicySpec,
        candidate_policy: PolicySpec,
        frozen_corpus: Sequence[Mapping[str, Any]],
        effect_factory: EffectFactory,
        *,
        invariant_checker: InvariantChecker | None = None,
    ) -> TemporalCompositionResult:
        """Prove disable -> replace -> clean-rebuild on an isolated snapshot.

        The effect ledger is the derived effect projection for this kernel
        slice. The source store's raw events are copied once, and all policy
        lifecycle changes are appended only to the isolated effect ledger.
        A fresh ledger instance then rebuilds the candidate projection from
        the append-only effect history. No held-out corpus or live store is
        available to the candidate.
        """

        cases = tuple(frozen_corpus)
        sandbox_store = Store(":memory:")
        sandbox_store.init_schema()
        sandbox_store.append_events(self.source_store.list_events())
        raw_before = _raw_history_hash(sandbox_store)
        ledger = EffectLedger(sandbox_store)
        regressions: list[str] = []
        reasons: list[str] = []
        try:
            old_effects = _append_policy_effects(ledger, old_policy, cases, effect_factory)
            old_projection_hash = ledger.state_hash()
            for effect in old_effects:
                ledger.deactivate(
                    effect.effect_id,
                    context_hash=effect.context_hash,
                    reason="temporal policy replacement",
                )
            disabled_projection_hash = ledger.state_hash()
            candidate_effects = _append_policy_effects(
                ledger, candidate_policy, cases, effect_factory
            )
            candidate_projection_hash = ledger.state_hash()
            rebuilt_ledger = EffectLedger(sandbox_store)
            rebuilt_projection_hash = rebuilt_ledger.state_hash()
            raw_after = _raw_history_hash(sandbox_store)

            if candidate_policy.status == PolicyStatus.ACTIVE:
                regressions.append("CANDIDATE_ALREADY_ACTIVE")
            if raw_before != raw_after:
                regressions.append("TEMPORAL_COMPOSE_MUTATED_RAW_HISTORY")
            if rebuilt_projection_hash != candidate_projection_hash:
                regressions.append("CLEAN_REBUILD_MISMATCH")
            active_policy_ids = {
                effect.policy_id for effect in rebuilt_ledger.active()
            }
            if active_policy_ids - {candidate_policy.policy_id}:
                regressions.append("OLD_POLICY_EFFECT_REMAINED_ACTIVE")
            if invariant_checker is not None:
                regressions.extend(invariant_checker(old_policy, candidate_policy, cases))
            if regressions:
                decision = SandboxDecision.REJECT
                reasons.append("INVARIANT_REGRESSION")
            else:
                decision = SandboxDecision.HUMAN_GATE_REQUIRED
                reasons.append("TEMPORAL_COMPOSITION_REPLAY_PASS")
                reasons.append("CLEAN_REBUILD_MATCH_PASS")
                reasons.append("NO_AUTOMATIC_PRODUCTION_HOT_SWAP")
            return TemporalCompositionResult(
                decision=decision,
                old_policy_id=old_policy.policy_id,
                candidate_policy_id=candidate_policy.policy_id,
                old_projection_hash=old_projection_hash,
                disabled_projection_hash=disabled_projection_hash,
                candidate_projection_hash=candidate_projection_hash,
                rebuilt_projection_hash=rebuilt_projection_hash,
                raw_history_hash_before=raw_before,
                raw_history_hash_after=raw_after,
                invariant_regressions=tuple(regressions),
                reason_codes=tuple(reasons),
            )
        finally:
            sandbox_store.close()


def _raw_history_hash(store: Store) -> str:
    return sha256_json([(event.id, event.content_hash) for event in store.list_events()])


def _append_policy_effects(
    ledger: EffectLedger,
    policy: PolicySpec,
    cases: Sequence[Mapping[str, Any]],
    effect_factory: EffectFactory,
) -> list[EffectRecord]:
    effects: list[EffectRecord] = []
    for case in cases:
        effect = effect_factory(policy, case)
        if effect is not None:
            ledger.append(effect)
            effects.append(effect)
    return effects


def _callable_hash(effect_factory: EffectFactory) -> str:
    try:
        source = inspect.getsource(effect_factory)
    except (OSError, TypeError):
        source = repr(effect_factory)
    return sha256_json({"module": getattr(effect_factory, "__module__", ""), "source": source})
