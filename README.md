# Open Possibility Lab

**A provenance-first workspace for research collaboration.** Keep the raw history, preserve
dissent, make closure reopenable, and let people refuse, leave, or take their work away.

**Status:** research prototype · synthetic evidence only · E1 structural evidence · no production
or user-benefit claim.

---

## Why this exists

Most AI collaboration tools are organized around the next answer: summarize the meeting, rank the
documents, produce the plan, keep the user engaged. That is useful, but it quietly decides what
counts as the problem.

This project began from a different observation. Research teams do not only lose information. They
lose the conditions under which information could become an interpretation:

1. **Directional isolation.** Different inquiries produce local results that never meet, so the
   team has many observations and little shared orientation.
2. **Temporal mismatch.** Social phenomena move faster than the team's ability to revise its
   questions; yesterday's framing silently becomes today's answer.
3. **Information is saved, interpretation is lost.** A record can be complete and still not be
   understandable later, because the situation, scope, disagreement and reason for closure are
   gone.
4. **Strategic cognition concentrates.** A few people hold the overall picture while everyone else
   holds fragments, and the system turns that asymmetry into a product.

The response here is not a smarter assistant and not a team-level mind. It is an attempt to build
**external conditions** for open inquiry: history that cannot be silently rewritten, projections
that are always derived and rebuildable, dissent that is not averaged away, closure that is
temporary and reopenable, and an exit that is real.

> 让可能性自由进入现实，也允许现实不按照 Team Soul 的期待发展。
>
> Let possibility enter reality freely; and let reality refuse the expectation.

The original Chinese line used the historical internal name. In this repository the name is
**Open Possibility Lab**; the research object is the *conditions*, not a "soul" or a collective
subject.

---

## What is here

Two layers, deliberately separated:

### 1. Core runtime — src/team_soul

A model-neutral, provenance-first runtime for research records:

- append-only raw events as the only source of truth;
- disposable, rebuildable projections;
- scoped evidence and explicit uncertainty;
- dissent preserved as a first-class position;
- closure that is scoped, conditional, and reopenable;
- a strict boundary between a proposal and an authority state.

### 2. Open Workspace slice — examples/open-workspace

A small, runnable interaction that tests whether the four experiences can coexist without turning
openness into a ritual:

| Experience | What it does |
|---|---|
| Direct help | One action returns a deterministic local answer with citations and a status label. |
| Optional expansion | Sources, dissent, alternatives, unresolved evidence, revision, untrusted text and excluded items appear only when asked. |
| Change / refuse / stop | Change the question, reject the current question, or stop; no reason required, no persuasion, no refusal record. |
| Carry away | Markdown and JSON export with a hash, limits, and no host paths; readable without this project. |

The slice is synthetic and deterministic. It is **not** a model product, a chatbot benchmark, or a
claim that openness improves people.

---

## Design commitments

1. **History before summary.** A summary may help navigation; it must never become the truth that
   replaces the record.
2. **Projection is derived.** Any "team view" is a scoped, time-bounded, rebuildable view, not a
   second source of truth.
3. **Dissent is not noise.** Minority positions are protected, not compressed into an average.
4. **Closure is for action, not eternity.** Every closure carries scope, evidence, uncertainty and
   reopen conditions; reopening is normal.
5. **Provenance must bind decisions.** A source field that is only decoration is not provenance.
6. **Similarity is not transfer.** A nearby case is not an applicable case.
7. **Refusal is a valid output.** Stop, leave, and "I do not want this" are not failure states to
   be optimized away.
8. **Exit is portable.** The artifact must remain readable when the system is gone.
9. **No profiling by the back door.** Attention, refusal and non-adoption must not become a
   performance record.
10. **Do not turn a research prototype into a governance infrastructure.** The project has an
    explicit exit condition: if a simpler baseline does the same job, it should be downgraded.

---

## Current status

| Area | Result |
|---|---|
| Core test suite | 164 passed, 7 skipped in this public snapshot. The private upstream runs 226 passed, 3 skipped; the difference is authorised data and reports that are not distributed here. |
| Open Workspace tests | 14 passed (composer, export, authority firewall, launcher, AppTest, error state, forced-mode negative control) |
| Real browser walkthrough | Chrome + Playwright: download and external parse, expansion stability, untrusted text as text, stop terminal, clean reload; 0 console errors |
| Independent review | 43/43 browser checks; base and slice suites reproduced by an independent reviewer |
| G3 comparison | 3 tasks × 3 modes; no correctness gain from optional openness; optional mode added no measured burden when unused; forced mode added 8 clicks / 7 panels without changing the answer |
| Evidence level | E1 structural only: synthetic tasks, one deterministic responder |

**What this does not establish:** user benefit, cognition, trust, Taste, adoption, retention,
productivity, model superiority, a team-level entity, strong recursive self-improvement, or any
E2/E4 effect. There is no user study and no external product baseline in this repository.

---

## Quick start

Prerequisites: Python 3.12, a terminal.

    git clone https://github.com/peishenglai3-hash/open-possibility-lab.git
    cd open-possibility-lab
    python -m venv .venv
    .venv/Scripts/activate        # Windows
    # source .venv/bin/activate   # Linux / macOS
    pip install -e ".[dev]"
    pytest -q

Expected: 164 passed, 7 skipped in this public clone. The private upstream runs 226 passed, 3 skipped; the difference is authorised data and reports that are not distributed here. See docs/PROVENANCE.md.

### Run the Open Workspace slice

    cd examples/open-workspace
    PYTHONPATH=.
    python -m w03.run

Open http://127.0.0.1:8503. The launcher binds loopback only, disables usage statistics, uses
typed error details and the minimal toolbar, and the app imports no authority modules.

### Run the slice tests and CLI replay

    cd examples/open-workspace
    PYTHONPATH=.
    python -m pytest tests -q
    python cli-replay/replay.py --task T1 --format md

### Reproduce the structural comparison

    cd examples/open-workspace
    PYTHONPATH=.
    python experiment/observed_workflow.py

The raw design and observed files are in examples/open-workspace/experiment. The comparison is
structural; it is not a user study.

---

## Repository map

    src/team_soul/                 core runtime: contracts, storage, projection, operators, kernel
    tests/                         core test suite
    fixtures/                      public structural fixtures C01-C05
    app/demo.py                    legacy three-column demo of the runtime
    examples/open-workspace/       the Week03 interaction slice (w03 package + Streamlit UI)
      w03/                         composer, export, guard, launcher, project loader
      gui-or-slice/                Streamlit app and loopback-safe config
      project/                     synthetic project (8 documents, 3 tasks)
      tests/                       14 slice tests
      experiment/                  design, preregistration, raw results, observed workflow addendum
      export-samples/              browser-produced Markdown/JSON samples
      cli-replay/                  headless replay
    docs/                          origin, architecture, research status, limits, roadmap, glossary
    FREEZE.md                      snapshot provenance and excluded assets
    FREEZE_MANIFEST.json           SHA256 manifest of this public snapshot

---

## Research program (short version)

### Week 01 — baseline

A deterministic structural baseline: raw history, projection rebuild, scoped retrieval, guarded
transfer, closure and reopen. No real team data. The question was whether the external organization
of history and evidence can be tested at all.

### Week 02 — E1 structural experiment

A pre-registered comparison over synthetic cases, with safety patches for input trust and privacy.
The experiment produced a **mixed/negative** result: the structural runtime did not demonstrate a
general reasoning advantage, and the project's own exit condition was kept visible. Known defects
remain; see docs/RESEARCH_STATUS.md.

### Week 03 — open interaction slice

The question shifted from "can the runtime win a benchmark" to "can the conditions be made
interactive without coercion". The slice implements the four experiences and was checked in a real
browser. G3 found a **zero correctness effect** for optional openness on three fixed tasks:
optional expansion did not improve answers, did not add burden when unused, and forced expansion
added cost without benefit. That is a useful negative result, not a success story.

The full research record, including failed and unfinished items, is summarized in
docs/RESEARCH_STATUS.md.

---

## Known limits (read this before citing anything)

- Synthetic only; no real team, real user, private corpus or external knowledge base.
- Deterministic responder; no language model is called by the slice.
- The 3-task G3 is a structural comparison, not a user study; its "oracle" is authored in the same
  project, so it establishes design consistency, not independent answer quality.
- The runtime's history is append-only by construction, but this is **not** a tamper-proof or
  hostile-process security claim. GATE-1/T2 and the Week02 D1/D2 defects remain open.
- OS-level egress is not blocked by this repository; the application has no network path and the
  tests do not call a provider. "No network in the app" is not "no network on the host".
- No CJK retrieval benchmark, no source-revocation lifecycle, no accessibility audit, no lockfile.
- No production deployment, no release, no security certification.

See docs/LIMITS_AND_SECURITY.md for the full list.

---

## Roadmap

Near term:

- pin the environment with a lockfile and document a clean-clone reproduction;
- add a real, external baseline and a small user study with ethics review (not synthetic-only);
- add source revocation that removes a source from display/export while retaining provenance;
- add CJK/locale fixtures and mixed-language refusal cases;
- add an accessibility pass and keyboard-only browser tests.

Conditional, not started:

- a local read-only knowledge base or a second agent, only after an explicit AMBER decision;
- an integration with an editor/DSH surface through a file/CLI contract, not a privileged plugin.

The project does **not** plan production self-modification, automatic promotion, live personal data,
or a "team soul" claim.

---

## Name and provenance

The repository is named **Open Possibility Lab** because the north star is not a smarter team
entity; it is the set of conditions under which possibility can enter without being coerced into a
single expected future.

The Python package retains the historical name team_soul for continuity with the frozen runtime
and its tests. That is a package name, not a claim that a team has a soul. The repository name and
the public framing are new.

- Original private workspace: HEAD a77f209 (main), frozen Week02 base a8dbc0f.
- This public snapshot is a curated subset, not the full private history. See docs/PROVENANCE.md.
- No private documents, owner notes, credentials, raw logs with host paths, or third-party corpora
  are included.

---

## License

- **Code:** MIT. See LICENSE.
- **Documentation and research notes:** CC BY 4.0. See LICENSE-CONTENT.md.
- Third-party material is not included. If you believe something was included in error, please open
  an issue or contact the maintainer.

## Contributing

Contributions are welcome if they preserve the boundaries above: no real personal data, no
authority writes from the GUI, no hidden networking, no claims that outrun the evidence. See
CONTRIBUTING.md and SECURITY.md.

## Citation

If you use this prototype in research, cite the repository and the snapshot described in
CITATION.cff and FREEZE.md. Do not cite it as evidence of user benefit or team-level cognition.
