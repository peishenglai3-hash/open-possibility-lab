# Architecture

## Two layers

### Core runtime: src/team_soul

A model-neutral, provenance-first runtime for research records. The name is historical; the design
is the point.

    raw events (append-only, hashed envelopes)
          |
          v
    storage (SQLite WAL + FTS5; raw tables are the source of truth)
          |
          +--> projection (derived, scoped, rebuildable)
          +--> retrieval (BASE / RAG / SOUL comparisons; protected dissent slots)
          +--> operators (transfer guard, closure, reopen, context assembly)
          +--> kernel (policy lifecycle, effects, receipts, sandbox, promotion)
          |
          v
    proposal / answer surface (cannot directly mutate authority state)

Important modules:

| Module | Responsibility |
|---|---|
| contracts.py | Pydantic contracts and invariants |
| storage.py | append-only raw events, projection tables, WAL, FTS5 |
| projection.py | deterministic rebuild from raw events |
| operators.py | TransferGuard, ClosureEngine, Reopen, scope checks |
| context.py | bounded context assembly with selection reasons |
| retrieval.py | retrieval arms and protected slots |
| kernel/ | policy registry, effects, receipts, sandbox, promotion |
| providers.py | mock and OpenAI-compatible boundary; no key is stored |
| eval*.py | historical evaluation harness; not a claim of validity |

### Open Workspace slice: examples/open-workspace

A small, standalone experiment that asks whether openness can be optional and exitable:

    project/synthetic_project.json
          |
          v
    w03/composer.py  (deterministic; no model, no network)
          |
          +--> answer + evidence + dissent + alternatives + unresolved + revision + excluded
          +--> answer_hash (canonical; independent of presentation mode)
          |
          v
    gui-or-slice/streamlit_app.py
          +--> direct answer
          +--> on-demand expansion
          +--> change / reject / stop
          +--> Markdown / JSON export
          |
          v
    w03/export.py (allowlist, export_sha256, no host paths)

The slice imports only its own w03 package. It does not import team_soul control-plane modules and
does not write closure, TEAM, policy, receipt, reopen, or raw-event authority.

## Invariants

1. Raw history is the only persisted source of truth.
2. Projection is derived and rebuildable; a projection is never the truth that replaces history.
3. Similarity does not authorize transfer.
4. Closure carries scope, evidence, uncertainty, and reopen conditions.
5. Reopen is a normal operation, not a failure.
6. Dissent is protected; it is not compressed into an average.
7. Provenance must bind a decision, not decorate it.
8. Model/provider output is a proposal; it cannot directly mutate authority state.
9. The action surface must offer a real exit: stop, refuse, leave, export.
10. A research prototype must not silently become a governance infrastructure.

## Authority boundary

The core runtime can write authority state through explicit operators. The Open Workspace slice is
deliberately **read-only over synthetic evidence and session state**:

- no import of closure/TEAM/policy/receipt/reopen modules;
- no database writes from the GUI;
- stop and refusal are terminal UI states, not records;
- export is built from raw composition output with an allowlist.

This boundary is tested by tests/test_w03_core.py and by the independent browser walkthrough.

## Testing strategy

| Layer | Evidence |
|---|---|
| Core contracts/runtime/operators/kernel | 164 passed / 7 skipped in the public snapshot; private upstream 226 passed / 3 skipped (authorised data/reports not distributed) |
| Slice composer/export/guard/launcher | 14 tests including AppTest and an error state |
| Real GUI | Chrome + Playwright: direct answer, expansion, untrusted text, downloads, stop, reload |
| Structural comparison | G3: 3 tasks × 3 modes; design-specified table and a post-hoc observed workflow addendum |
| Independent review | Reviewer reproduced the suites and 43/43 browser checks |

Passing tests are not user evidence. A health endpoint is not GUI acceptance. A screenshot is not a
user study.

## Loopback and data flow

The slice launcher binds 127.0.0.1 only, disables usage statistics, uses typed error details and the
minimal toolbar, and enables XSRF/CORS protections. The app serves a static shell plus a WebSocket
data channel; a plain GET of / returns only the shell. The configuration is in
examples/open-workspace/gui-or-slice/.streamlit/config.toml.

## Why not a plugin

The Week03 decision record rejected a privileged DSH/editor plugin for this step because of
ownership, permission, restart, and authority-surface concerns. A future integration should use a
file/CLI contract with an explicit boundary, not a plugin with ambient access.

## Where the next change goes

- source revocation: a data lifecycle contract, not a hidden flag;
- real baseline/user study: a separate protocol with ethics review;
- CJK/locale: fixtures and a retrieval contract;
- environment lock: a lockfile and a clean-clone reproduction;
- optional KB/agent: only after an explicit AMBER decision and a security review.
