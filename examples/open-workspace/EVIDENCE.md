# Evidence for the Open Workspace slice

This is a compact evidence record. Raw logs with host paths are excluded from the public snapshot;
the commands below are reproducible from the included code.

## Environment

- Python 3.12.14
- pydantic 2.14.1
- Streamlit 1.66.0
- Chrome + Playwright used for the browser walkthrough
- Server bind: 127.0.0.1:8503 only

The environment is documented, not lockfile-pinned. A lockfile is future work.

## Tests

Core runtime, from the repository root:

    PYTHONPATH=src
    pytest -q

Result in this public snapshot: 164 passed, 7 skipped. The private upstream runs 226 passed, 3
skipped; the difference is authorised E2/corpus data and evaluation reports that are not
distributed here. The skips are not passes.

Open Workspace slice:

    cd examples/open-workspace
    PYTHONPATH=.
    python -m pytest tests -q

Result: 14 passed.

## Browser walkthrough

The private workspace ran Chrome + Playwright against the loopback server. The observed checks were:

| Check | Result |
|---|---|
| Direct answer | pass |
| Expand sources and answer stability | pass; answer text and hash unchanged |
| Untrusted text | rendered as text, not instruction |
| Markdown download + external parse | pass |
| JSON download + external parse | pass; export_sha256 present |
| Stop terminal | pass; no answer controls remain |
| Reload | clean state, no answer |
| Console errors | 0 |

An independent reviewer reproduced 43/43 browser checks in the same environment.

## G3 structural comparison

Preregistration: 5 files hashed before the run; the independent reviewer verified 5/5 hashes and
9/9 result rows.

Design-specified modes:

| Mode | Design steps | Design panels |
|---|---:|---:|
| BASELINE | 1 | 0 |
| OPTIONAL | 1 | 0 |
| FORCED | 3 phases | 7 |

Observed workflow addendum (post-hoc, AppTest, not preregistered):

| Mode | Clicks to answer | Panels before answer | Answer hash |
|---|---:|---:|---|
| BASELINE | 1 | 0 | 25e1f12e... |
| OPTIONAL | 1 | 0 | 25e1f12e... |
| FORCED | 8 | 7 | 25e1f12e... |

All three tasks produced the same answer hash across modes. The composer status matched its own
project-authored rule on 3/3 tasks; that is design-consistency, not an independent correctness
oracle. Distinct cited IDs are 6/3/3 for T1/T2/T3; the raw occurrence count is 6/4/4.

## What is not evidence

- A passing test suite is not a user study.
- A browser screenshot is not GUI acceptance by itself.
- G3 is not a human cognitive-load measurement; clicks/panels are structural proxies.
- The 3-task zero effect does not prove a general "no effect"; it is a result on fixed tasks.
