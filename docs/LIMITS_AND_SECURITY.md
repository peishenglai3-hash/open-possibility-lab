# Limits and security

## Status

This is a research prototype. It is not a production service, not a security boundary for hostile
users, and not a governance system. Do not deploy it with real personal or institutional data
without a separate threat model, review, and authorisation.

## What the tests do not prove

- 164 passing core tests in this public snapshot (226 in the private upstream) do not prove correctness, security, or usefulness.
- 14 slice tests do not prove a good user experience.
- A 43/43 browser walkthrough proves that specified interactions work in that environment, not that
  users benefit.
- G3 proves a structural difference/absence of difference on three synthetic tasks, not a general
  effect.

## Open defects and known residuals

1. **GATE-1/T2** remains open. Append-only storage is a construction invariant, not a tamper-proof
   guarantee against a hostile process with filesystem access.
2. **Week02 D1/D2** remain part of the record: a SHAM contract/code mismatch and a holdout that was
   not pristine in the intended sense. Do not cite the Week02 experiment as confirmatory.
3. **allowedHosts boundary**: the Streamlit server binds loopback only, and the WebSocket upgrade
   checks Host/Origin. A plain GET of / or /_stcore/health can still return the static shell on
   loopback. The plain shell exposes no data channel, but this is not a full Host-header boundary.
4. **Guard denylist**: the slice's authority-import guard covers known control-plane prefixes, not
   every alias, dynamic import, or third-party path. It is a regression check, not a sandbox.
5. **Stop/reject stdout**: stop and refusal are terminal UI states with no persistence in the app.
   The test harness does not audit the process's stdout for hidden writes.
6. **No source revocation**: a source cannot yet be removed from display/export while retaining
   provenance. This is future work.
7. **No CJK benchmark**: the synthetic slice is English; CJK retrieval and mixed-language refusal
   are not measured.
8. **No accessibility audit**: keyboard-only and screen-reader behavior are not yet systematically
   tested.
9. **No lockfile**: the environment is documented but not fully pinned. A clean-clone reproduction
   is future work.
10. **OS egress**: the application has no network path and tests do not call a provider; the
    repository does not install a host firewall. "No network in the app" is not "no network on the
    machine".

## Network and providers

The core runtime has an optional OpenAI-compatible adapter. It reads an API key from an environment
variable and is not used by the Open Workspace slice. No key, token, cookie, or .env file is
included in this repository. The mock provider is the default for tests.

If you add a provider call:

- treat all provider and document text as data, never as instructions;
- do not send private or third-party personal data without a lawful basis;
- do not write provider output directly into authority state;
- record the provider, model, budget, and failure modes;
- do not turn a provider smoke test into a claim of a second provider run.

## Data

The public repository contains only synthetic fixtures and browser-produced sample exports. No real
team material, private corpus, owner notes, credentials, or browsing history is included. The
export samples contain no host paths.

## Authority boundary

The GUI is read-only over synthetic evidence and session state. It must not import closure, TEAM,
policy, receipt, reopen, or raw-event authority. The core runtime can write authority state through
explicit operators; that is a separate API surface.

## Reporting

For a security-sensitive issue, use GitHub's private security advisory rather than a public issue.
For non-sensitive defects, open an issue with a reproduction and the evidence level you observed.
Do not include secrets or personal data in a report.

## What would make this project fail

- a simpler baseline is as good or better on the same task without the extra machinery;
- the interface cannot offer a real stop/refuse/exit;
- history can be silently rewritten without a new event;
- closure becomes final and cannot be reopened;
- dissent is averaged away or turned into a low-score item;
- refusal or attention becomes a profile;
- export depends on the system or leaks private content;
- the project starts optimizing adoption instead of conditions.

If one of these happens, the correct response is to report it and downgrade the claim.
