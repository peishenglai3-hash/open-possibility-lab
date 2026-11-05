# Security policy

This is a research prototype. Do not deploy it as a production service or with real personal,
institutional, or confidential data.

## Reporting

For a security-sensitive issue, use GitHub's private security advisory for this repository rather
than a public issue. For non-sensitive defects, open an issue with a reproduction and the evidence
level you observed.

Do not include credentials, personal data, or private logs in a report.

## Scope of the current boundary

- The slice is a local read-only Streamlit app over synthetic evidence and session state.
- It binds loopback only and imports no authority modules.
- Stop and refusal are terminal UI states with no persistence in the app.
- Export is allowlisted, synthetic, and contains no host paths.
- The runtime's append-only storage is not a tamper-proof guarantee.
- GATE-1/T2, Week02 D1/D2, the allowedHosts plain-GET shell, the guard denylist, the stop/reject
  stdout audit gap, source revocation, CJK, and accessibility remain open as described in
  docs/LIMITS_AND_SECURITY.md.

## Supported versions

Only the current public snapshot is supported. There is no LTS or production support commitment.
