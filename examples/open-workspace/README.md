# Open Workspace slice

A small, synthetic, deterministic interaction that implements the four experiences from the Week03
research contract. It is not a model product and not a user study.

## Run

    cd examples/open-workspace
    PYTHONPATH=.
    python -m w03.run

Then open http://127.0.0.1:8503. The launcher binds 127.0.0.1 only, disables usage statistics, uses
typed error details, and keeps the minimal toolbar. The app imports no team_soul control-plane
module.

## Four experiences

1. **Direct answer**: one button, a deterministic answer with citations and a status label.
2. **Optional expansion**: sources, dissent, alternatives, unresolved evidence, revision, untrusted
   text, and excluded items, only on request.
3. **Change / refuse / stop**: change the question, reject the current question, or stop; no reason,
   no persuasion, no persistent refusal record.
4. **Carry away**: Markdown/JSON download with an allowlist and export_sha256; no host paths.

## Tests

    cd examples/open-workspace
    PYTHONPATH=.
    python -m pytest tests -q

Expected: 14 passed.

Real browser walkthrough (Chrome + Playwright, from the private workspace) checked:

- direct answer and stable answer after expansion;
- untrusted text rendered as text, not instruction;
- Markdown and JSON downloads parsed outside the app;
- stop terminal state and clean reload;
- 0 console errors.

That walkthrough is TESTED evidence, not a user study.

## Structural comparison

    PYTHONPATH=.
    python experiment/observed_workflow.py

Observed: BASELINE 1 click / 0 panels; OPTIONAL 1 / 0; FORCED 8 clicks / 7 panels. The answer hash is
identical in all modes. The design table and the observed addendum are different units and are not
compared numerically.

## Files

    w03/                 composer, export, guard, launcher, project loader
    gui-or-slice/        Streamlit app and loopback-safe config
    project/             synthetic project (8 documents, 3 tasks)
    tests/               14 tests
    experiment/          design, preregistration, raw results, observed addendum
    export-samples/      browser-produced Markdown/JSON
    optional-spike/      G4 NOT_RUN record
    cli-replay/          headless replay

## Limits

- Synthetic only; no real team, user, provider, network, or external KB.
- Deterministic responder; no language model is called.
- The G3 "oracle" is authored in the same project; it is design-consistency, not independent
  correctness.
- The app's authority firewall is a regression check, not a sandbox.
- Stop/refusal are not persisted; process stdout is not audited.
- No source revocation, CJK benchmark, accessibility audit, or lockfile.
- OS-level egress is not blocked by this repository.

See the root docs/LIMITS_AND_SECURITY.md for the full list.
