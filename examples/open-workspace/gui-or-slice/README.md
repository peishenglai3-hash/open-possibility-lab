# Streamlit slice — run and configuration

The slice is launched from the parent directory with:

    cd examples/open-workspace
    PYTHONPATH=.
    python -m w03.run

The launcher uses w03/launcher.py to bind 127.0.0.1 only, disable usage statistics, use typed error
details, keep the minimal toolbar, and pass explicit server flags. The Streamlit config in
.streamlit/config.toml is part of the safety boundary.

## What is tested

- direct answer, optional expansion, refuse/stop, export;
- real Chrome walkthrough in the private workspace;
- AppTest for state transitions and error state;
- a forced-mode negative control used only by the observed workflow addendum.

## What is not tested here

- accessibility and screen-reader behavior;
- multi-user or remote deployment;
- hostile-process security;
- real data, provider, or network;
- source revocation.

Do not expose this server beyond loopback without a separate security review.
