# Week03 synthetic export

- project: synthetic-memory-index
- task: T1
- status: CONDITIONAL
- synthetic: true (no real team data, no model, no network)

## Question
Should the synthetic project adopt the shared index?

## Answer
Adopt with conditions, not unconditionally. The synthetic pilot supports adoption [s1][s2], but a privacy dissent [s3] and the absence of independent external validation [s6] remain. A federated alternative [s4] should be compared, and the earlier adoption decision was reversed [s8]. Do not treat this as a team decision.

## Evidence
- [s1] Pilot latency note (FACT): The synthetic pilot reduced median search time by 18% across two projects.
- [s2] Cost estimate (FACT): The synthetic cost estimate is two engineer-weeks for the first rollout.

## Dissent
- [s3] Privacy objection (DISSENT): A pilot participant reports that a private note appeared in the index and the opt-out was not honoured.

## Alternatives
- [s4] Federated alternative (CANDIDATE): A federated index could keep each project local while sharing only explicit references.

## Unresolved
- [s6] Missing external validation (UNRESOLVED): No independent external validation of the synthetic pilot exists.

## Revision
- [s8] Revision note (REVISION): An earlier synthetic decision to adopt was reversed after the privacy incident; the revision remains visible.

## Limits
- Synthetic project authored for Week03; not a real team or real index.
- The responder is deterministic; no model, no network, no provider.
- The slice is read-only over synthetic evidence and session state; it writes no authority state.

## Integrity
- export_sha256: 5915d54b2885be07546ae0a81dc03d019bce2fdff5ccb1fdcfe48ef475344995
