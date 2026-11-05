# Glossary

**Raw event**: an append-only record with a hashed envelope; the only persisted source of truth.

**Projection**: a derived, scoped, time-bounded, rebuildable view. It is not a second truth.

**Closure**: a temporary operational decision with scope, evidence, uncertainty, and reopen
conditions. It is made for action, not to end inquiry.

**Reopen**: a normal operation that reopens a closure without deleting the history that produced it.

**TEAM**: a guarded scope promotion; it is not a headcount or a majority vote. The runtime may
return NO_CLOSURE when scope, provenance, validation, actor coverage, or reopen conditions are
missing.

**Dissent**: a protected position that differs from the apparent majority; it must not be averaged
away or treated as a failure by default.

**Transfer**: moving a claim or pattern from one scope to another. Similarity alone does not
authorize transfer.

**Provenance**: a binding relation between a decision and the material/conditions that produced it,
not a decorative source field.

**Exposure**: what an actor has actually seen; not the same as authorization or membership.

**Evidence level**: BUILT, TESTED, EXPERIMENTALLY_OBSERVED, E1-E4, NOT_OBSERVED, DEFERRED. Higher
levels cannot be inferred from lower ones.

**R1**: the openness mechanism research question. In this project its status is EMPIRICAL_HOLD.

**R2**: the strong Taste/subjectivity question. In this project its status is design-only.

**Optional expansion**: sources, dissent, alternatives, unresolved evidence, revision, untrusted
text and excluded items are available on request, not demanded before a direct answer.

**Refusal / stop**: a terminal interaction state with no reason required, no persuasion, no
follow-up, and no persistent refusal record.

**Portable export**: Markdown/JSON output with an allowlist, a hash, limits, and no host paths,
readable without the runtime.

**Zero-effect result**: the comparison found no difference in the measured outcome. It is a result,
not a failure to be hidden.
