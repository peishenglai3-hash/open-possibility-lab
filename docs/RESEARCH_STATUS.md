# Research status

This document records what was tested, what was not, and what the project refuses to claim. It is
intentionally less flattering than a launch page.

## Evidence levels used here

| Level | Meaning |
|---|---|
| BUILT | code exists |
| TESTED | automated or browser checks pass |
| EXPERIMENTALLY_OBSERVED | controlled comparison with fixed material |
| E1 | structural fixture/experiment evidence |
| E2 | historical replay or independent-field evidence (not observed here) |
| E4 | real user / live setting (not observed here) |
| NOT_OBSERVED | no evidence; do not infer success |

No level may be skipped. A passing AppTest is TESTED. A browser walkthrough is TESTED. Neither is a
user study.

## Week 01 — structural baseline

Built a deterministic runtime for:

- append-only raw events with hashed envelopes;
- projection rebuild;
- scoped retrieval with protected dissent slots;
- guarded transfer (similarity is not applicability);
- closure with scope/evidence/reopen conditions;
- reopen/revoke/fork/downgrade;
- context assembly with selection reasons.

The baseline established that the structural properties can be implemented and tested. It did not
establish real team benefit, reasoning superiority, or a team-level entity.

## Week 02 — E1 experiment and safety patches

A pre-registered structural comparison was run over synthetic cases, with safety work around
untrusted input and privacy. The result was mixed/negative:

- the runtime did not demonstrate a general reasoning advantage under the frozen comparison;
- several evaluation and boundary defects were found and are still part of the record;
- two known issues, D1 (a SHAM contract/code mismatch) and D2 (a holdout that was not pristine in
  the intended sense), limit what the Week02 experiment can support;
- GATE-1/T2 remains open: the system does not claim tamper-proof history or hostile in-process
  security;
- the Week02 experiment is **not** cited here as confirmatory evidence.

These are kept rather than rewritten. A project that only publishes its successes is not a
research record.

## Week 03 — open interaction slice

The question moved from benchmark performance to interaction conditions. The slice implements four
experiences over a synthetic project:

1. direct answer;
2. optional expansion;
3. change / refuse / stop;
4. portable Markdown/JSON export.

### What was tested

| Test | Result |
|---|---|
| Slice test suite | 14 passed |
| Core suite at the frozen base | Private upstream: 226 passed / 3 skipped. Public snapshot: 164 passed / 7 skipped (authorised-data modules are skipped, not passed). |
| Real Chrome walkthrough | direct answer, expansion stability, untrusted text, download + external parse, stop, reload; 0 console errors |
| Independent browser check | 43/43 checks reproduced |
| Authority firewall | app imports no team_soul control-plane module; no authority writes |
| Bind/config | 127.0.0.1 only; no usage stats; typed errors; minimal toolbar; XSRF/CORS |

### G3 comparison

Three fixed synthetic tasks, three presentation modes:

| Mode | Design steps (specified) | Observed clicks | Correctness effect |
|---|---:|---:|---|
| BASELINE | 1 | 1 | reference |
| OPTIONAL | 1 | 1 | none |
| FORCED (negative control) | 3 phases | 8 clicks / 7 panels | none |

The design table is a specification, not a measurement. The observed addendum was added after the
run and is exploratory, not preregistered.

Result:

- optional openness produced **no correctness gain** on these tasks;
- optional openness added **no measured burden when unused** (1 click / 0 panels, same as baseline);
- forced openness added **8 clicks and 7 panels** without changing the answer;
- the answer hash was identical across modes;
- no wrong-scope or untrusted item was cited in the answer.

This is a zero-effect result. It supports the design choice "optional, not ritual"; it does not
support "openness improves outcomes".

### What is not observed

- no real user, no user study, no external product baseline;
- no cognition, trust, learning, Taste, adoption, retention or productivity measurement;
- no CJK retrieval benchmark;
- no source-revocation lifecycle;
- no accessibility audit;
- no OS-level network isolation claim;
- no production security certification.

## The project's own exit condition

If a simpler baseline does the same job, the correct response is to downgrade the project. Week03's
G3 is consistent with that discipline: on simple tasks, optional expansion is not needed, and forcing
it is a cost. The remaining research question is not "how do we make people explore more?" but
"when, if ever, does preserved history/dissent/reopenability change a judgement that a simpler
baseline would get wrong?"

That question is not answered here.

## Claim status

- R1 (openness mechanism): EMPIRICAL_HOLD.
- R2 (Taste/strong subjectivity): design-only.
- E2 historical replay: NOT_OBSERVED.
- E4 real user effect: NOT_OBSERVED.
- Team-level entity / collective consciousness: not assumed, not claimed.
- Strong recursive self-improvement: not claimed.

## Reproducibility

The public snapshot includes the core source, tests, fixtures, the slice, its tests, and the raw
experiment files. See FREEZE.md and FREEZE_MANIFEST.json for the exact snapshot. A clean-clone
lockfile is still future work.
