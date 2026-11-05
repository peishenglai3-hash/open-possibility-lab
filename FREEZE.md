# Freeze record

**Snapshot:** Open Possibility Lab 0.3.0
**Date:** 2026-11-05
**Private source:** main HEAD a77f209; frozen Week02 base a8dbc0f
**Public history:** a new single initial commit; the private history is not exported.

## What this snapshot is

A curated public subset of the private research workspace:

- core runtime source and tests;
- public structural fixtures C01-C05;
- the Week03 Open Workspace slice, its tests, synthetic project, experiment files, and browser
  export samples;
- new public documentation, licenses, and provenance record.

## What is excluded

- .env files, credentials, tokens, cookies, API keys;
- private corpora, owner notes, internal review documents;
- raw logs with absolute host paths;
- private evaluation result dumps and receipts;
- pre-Week03 security exploit probes and adversarial scripts;
- third-party material whose rights were not cleared for redistribution.

## Public-snapshot test results

| Suite | Result |
|---|---|
| Core, public snapshot | 164 passed, 7 skipped |
| Core, private upstream | 226 passed, 3 skipped |
| Open Workspace slice | 14 passed |
| Browser walkthrough | 43/43 independent checks; 0 console errors |

The public core suite is smaller because six test modules and one test require authorised E2/corpus
data or Week02 evaluation reports that are not distributed. They are skipped, not passed. The
private upstream has that data and therefore runs 226/3.

## Public-snapshot code adaptations

Two minimal adaptations were required for a redistributable snapshot:

1. Tests that require authorised E2/corpus data or private evaluation reports skip at module/test
   level when the data is absent. They still run normally in the private workspace.
2. The CLI demo-inspect command now uses .get for the optional failure-candidate count, so a public
   checkout without the private failure register does not crash on a missing key. This is a
   robustness fix, not a semantic change.

No authority, storage, projection, or closure semantics were changed for the public snapshot.

## Integrity

The exact file set and SHA256 hashes are in FREEZE_MANIFEST.json. The manifest excludes itself and
.git metadata. A local archive of this snapshot may be kept separately; the GitHub repository is the
public copy.

## Limits

This is a research prototype with synthetic evidence only. It does not establish user benefit,
cognition, Taste, adoption, retention, productivity, model superiority, a team-level entity, or
strong RSI. Known defects remain; see docs/LIMITS_AND_SECURITY.md.

## License

Code: MIT. Documentation and research notes: CC BY 4.0. Third-party material is not intentionally
included.
