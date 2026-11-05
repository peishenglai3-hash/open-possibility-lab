# Provenance

## Source

This public repository is a curated snapshot of a private research workspace.

| Item | Value |
|---|---|
| Private main HEAD | a77f209 |
| Frozen Week02 base (P0 patches) | a8dbc0f |
| Public snapshot date | 2026-11-05 |
| Snapshot type | curated source + tests + synthetic slice + new documentation |
| Public history | a new public history (bootstrap README + snapshot commit); not the private commit history |

The private history is not published because it contains internal review notes, owner documents,
raw logs with host paths, and material whose rights were not cleared for redistribution.

## What is included

- core runtime source: src/team_soul;
- core tests and public fixtures;
- legacy app/demo.py;
- the Week03 Open Workspace slice, its tests, project, experiment files, and export samples;
- new documentation written for this public snapshot.

## What is excluded

- .env, credentials, tokens, cookies, API keys;
- private corpora, owner notes, internal review documents;
- raw logs containing absolute host paths;
- evaluation result dumps and receipts from the private workspace;
- pre-Week03 security exploit probes and adversarial scripts;
- third-party material whose license was not verified.

## AI assistance

The code and documentation were produced in a human-directed, AI-assisted engineering process. The
human owner set the research direction, boundaries, and approvals; the AI agent wrote and tested
code, ran experiments, and drafted documents. This is disclosed so that readers do not mistake the
snapshot for unaided authorship.

The package name team_soul is retained for continuity. The public repo name, framing, and README are
new.

## Rights and license

The private workspace had no LICENSE. For this public snapshot the owner releases:

- code under MIT (LICENSE);
- documentation and research notes under CC BY 4.0 (LICENSE-CONTENT.md).

No third-party material is intentionally included. If you believe something was included in error,
open an issue or contact the maintainer.

## Freeze

The exact file set and SHA256 hashes are in FREEZE_MANIFEST.json. FREEZE.md records the test results,
exclusions, and the limits of the snapshot. A local archive may be created for long-term storage;
the GitHub repository is the public copy.
