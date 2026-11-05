# Roadmap

The roadmap is ordered by evidence value, not by feature count. Every item must state what would
falsify it.

## Now (public snapshot)

- The core runtime and the Open Workspace slice are frozen at the commits described in FREEZE.md.
- The public snapshot is synthetic-only.
- Tests: public core 164 passed / 7 skipped; private upstream 226 passed / 3 skipped; slice 14 passed; browser walkthrough 43/43 independent
  checks.
- G3: optional openness has no correctness effect on three fixed tasks; forced openness costs
  clicks/panels.

## Near term

### 1. Environment lock and clean-clone reproduction

Add a lockfile and a script that reproduces the core and slice suites from a fresh clone without
network access. Falsifier: a clean clone cannot reach the recorded test counts.

### 2. Real external baseline and small user study

Compare the optional-expansion slice with a genuinely simpler external workflow, and later with
consenting users under an ethics protocol. Measure task correctness, time, refusal behavior, and
comprehension of limits. Falsifier: the simpler baseline is equal or better with less burden.

This is the most important item. Until it exists, the project has no user evidence.

### 3. Source revocation

Implement a lifecycle in which a source can be revoked: removed from display and export while its
provenance and the fact of revocation remain visible. Falsifier: revocation deletes history or
cannot be distinguished from never having existed.

### 4. CJK and locale

Add CJK fixtures, mixed-language requests, and refusal cases that do not depend on English token
assumptions. Falsifier: the slice behaves differently for equivalent Chinese tasks without a
documented reason.

### 5. Accessibility and keyboard-only path

Test the slice with keyboard-only navigation and a screen reader; add focus and label tests.
Falsifier: a core experience cannot be completed without a mouse.

## Conditional (not started)

### A local read-only knowledge base

Only after an explicit AMBER decision and a security review. The KB must be local, read-only,
synthetic or explicitly licensed, and unable to mint authority state. Falsifier: the KB adds cost
without a measurable change in a named task.

### A second agent

Only after the same gate. It must not be a privileged plugin with ambient access; it should use a
file/CLI contract and be evaluated as an independent proposal source. Falsifier: the second agent
increases confidence without increasing correctness, or it cannot be stopped.

### Editor/DSH integration

A file/CLI contract, not a privileged plugin. The integration must preserve refusal and export even
when the host UI changes.

## Not planned

- production self-modification or automatic policy promotion;
- live personal data, profiling, or performance evaluation of users;
- a claim of team-level consciousness or a "team soul";
- retention/adoption optimization;
- a public demo with real user content.

## Research gates

1. Any new claim needs a pre-registered task, a fixed baseline, a fixed budget, and a stated
   falsifier.
2. A passing test is not an observed effect; an observed effect is not a user benefit.
3. Negative and null results stay in the record.
4. A simpler baseline may win. If it does, the project is downgraded.
5. No production claim without a separate security and ethics review.
