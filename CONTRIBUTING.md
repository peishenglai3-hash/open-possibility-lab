# Contributing

Thanks for taking the project seriously enough to inspect its limits.

## Before you start

- Read docs/PROJECT_ORIGIN.md, docs/ARCHITECTURE.md, and docs/LIMITS_AND_SECURITY.md.
- Do not use real personal data, private corpora, credentials, or live provider calls in tests.
- Do not add a GUI path that writes closure, TEAM, policy, receipt, reopen, or raw-event authority.
- Do not turn refusal, attention, or non-adoption into a profile.
- Do not add a claim that outruns the evidence level.

## Workflow

1. Open an issue describing the problem, the evidence level, and the expected falsifier.
2. Keep changes scoped; preserve the existing tests and negative results.
3. Add tests for new behavior, including an error state or a refusal path where relevant.
4. Run the core suite and the slice suite.
5. State what you did not verify.

## Tests

From the repository root:

    PYTHONPATH=src
    pytest -q

Open Workspace slice:

    cd examples/open-workspace
    PYTHONPATH=.
    python -m pytest tests -q

## Style

- Python 3.12.
- Keep the authority boundary explicit.
- Prefer small, reviewable changes over broad rewrites.
- Do not add hidden network calls or telemetry.
- Document limitations in the same change, not in a later cleanup.

## Licensing

By contributing, you agree that code contributions are licensed under MIT and documentation
contributions under CC BY 4.0, consistent with LICENSE and LICENSE-CONTENT.md.
