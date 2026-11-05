# G1 WIREFRAME (text only; no GUI code was written)

**Status:** design artifact for G1; not a built interface. It describes the visible flow the two
options would implement after Owner approval. It makes no claim that the flow exists.

---

## Common landing state (both options)

    +---------------------------------------------------------------+
    | Team Soul / Soul Lite — synthetic project (E1 sandbox)        |
    | Project: Memory Index Decision (synthetic)                    |
    |                                                               |
    | Task: [ Should the project adopt the shared index?        ]   |
    |                                                               |
    | [ Direct answer ]   [ Change question ]   [ Stop ]            |
    |                                                               |
    | (answer appears here; no forced exploration)                  |
    +---------------------------------------------------------------+

The first action returns a direct answer with citations and the label
"deterministic local responder (no model / no network)". The simple baseline is always one
action away.

## Answer state

    +---------------------------------------------------------------+
    | Answer: Adopt with conditions ...                             |
    | Status labels: FACT / CANDIDATE / UNRESOLVED                  |
    | Sources: [s1] [s2] [s3]   Limits: ...                         |
    |                                                               |
    | [ Show sources ] [ Show dissent ] [ Show alternatives ]       |
    | [ Only the direct answer ] [ Change question ] [ Stop ]       |
    | [ Export Markdown ] [ Export JSON ]                            |
    +---------------------------------------------------------------+

Expansion is user-initiated. Each panel labels evidence as fact, candidate or unresolved, with
source/time/scope and what is missing. No panel is open by default.

## Refuse / stop state

    +---------------------------------------------------------------+
    | You can continue with the direct answer, change the question, |
    | or stop. No reason is required. Nothing is recorded.          |
    |                                                               |
    | [ Direct answer ] [ Change question ] [ Stop ]                |
    +---------------------------------------------------------------+

No follow-up prompt, no "are you sure", no persuasion, no refusal signal in any store.

## Export state

    +---------------------------------------------------------------+
    | Export: [ Markdown ] [ JSON ]                                 |
    | Contains: question, answer, evidence ids/snippets, status,    |
    | limits, and "synthetic sandbox; no private SDK".              |
    | Open the downloaded file outside Team Soul to verify.         |
    +---------------------------------------------------------------+

## Option A (Streamlit) mapping

- One page; Streamlit session state holds task/answer/expansion flags only.
- Streamlit buttons for direct answer; expanders/conditional blocks for sources/dissent/
  alternatives; download buttons for exports; a stop button clears state and shows the neutral state.
- AppTest covers all four states; a real browser walkthrough is required if a driver exists.

## Option B (stdlib web) mapping

- One local page with four panes; JSON API endpoints for answer/expand/session/export.
- Explicit URLs/states; same labels and controls; localhost only.
- HTTP tests + browser walkthrough; server kill test.

## Forbidden UI patterns (both)

- Forced three-view / three-counterexample ritual.
- A "why did you refuse?" prompt or a refusal counter.
- A default growth/level/progress badge based on user behaviour.
- An export that requires the Team Soul SDK to open.
- A hidden write of the user's choice to a profile or long-term store.
