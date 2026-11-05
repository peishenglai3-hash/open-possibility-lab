from __future__ import annotations

import streamlit as st

from team_soul.config import load_config
from team_soul.demo_runtime import run_demo_inspection
from team_soul.ingest import load_fixtures
from team_soul.kernel.policies import policy_catalog_markdown


st.set_page_config(page_title="Team Soul V0.2 Experiment Demo", layout="wide")
st.title("Team Soul / 团宠 — V0.2 Experiment Demo")
st.caption("Projection-first, provenance-first, model-neutral comparison")

runtime = load_config()
cases = load_fixtures("fixtures")
case_by_id = {case.case_id: case for case in cases}
case_id = st.selectbox("Fixture case", list(case_by_id))
case = case_by_id[case_id]
query = st.text_input("Query", case.query)
st.caption(
    "Fixture inspection is E1 structural evidence only. It does not claim a live pilot, "
    "real-world team benefit, or an active user policy."
)

allow_provider = False
if runtime.provider_kind != "mock":
    st.warning(
        "The configured provider will receive the same fixture query/evidence for BASE, RAG, SOUL, "
        "and five ablations (up to 18 calls). No live evaluation is run until you start it."
    )
    allow_provider = st.checkbox("I authorize this configured-provider experiment run")

run_clicked = st.button(
    "Run reproducible comparison",
    disabled=(runtime.provider_kind != "mock" and not allow_provider),
)
if run_clicked:
    with st.spinner("Running three arms and five ablations on the selected fixture…"):
        st.session_state["demo_inspection"] = run_demo_inspection(
            case, runtime, query=query, failure_report_path="docs/FAILURE_REGISTER.json"
        )
        st.session_state["demo_inspection_key"] = (case_id, query)

report = st.session_state.get("demo_inspection")
if report is None or st.session_state.get("demo_inspection_key") != (case_id, query):
    st.info(
        "Run the comparison to populate trace, policy, FailureScout, and ablation views. "
        "The CLI equivalent is `team-soul demo-inspect --case " + case_id + "`."
    )
    st.stop()

st.caption(f"Experiment hash: `{report['experiment_hash']}` · Evidence: `{report['evidence_level']}`")
st.caption(f"Projection hash: `{report['projection_hash']}` · Rebuild stable: `{report['projection_stable']}`")

arm_columns = st.columns(3)
for column, (arm, payload) in zip(arm_columns, report["arms"].items()):
    with column:
        st.subheader("TEAM SOUL" if arm == "SOUL" else arm)
        st.metric("Context tokens", payload["input_tokens"])
        st.metric("Selected evidence", len(payload["selected_event_ids"]))
        st.write(payload["response"])
        st.caption("Selected event IDs")
        st.code("\n".join(payload["selected_event_ids"]) or "NO_RETRIEVED_EVIDENCE")

trace_tab, policy_tab, failure_tab, ablation_tab = st.tabs(
    ["Trace / metrics", "Policy", "FailureScout", "Ablations"]
)
with trace_tab:
    st.subheader("Arm metrics")
    st.dataframe(
        [
            {"arm": arm, **payload["metrics"]}
            for arm, payload in report["arms"].items()
        ],
        width="stretch",
    )
    selected_arm = st.selectbox("Inspect source trace", list(report["arms"]), key="trace_arm")
    st.json(report["arms"][selected_arm]["source_trace"])
    st.json(
        {
            "cited_event_ids": report["arms"][selected_arm]["cited_event_ids"],
            "context_hash": report["arms"][selected_arm]["context_hash"],
            "experiment_hash": report["experiment_hash"],
        }
    )

with policy_tab:
    st.subheader("Implemented learnable seams")
    st.markdown(policy_catalog_markdown())
    st.json(report["policy_differences"])

with failure_tab:
    st.subheader("FailureScout candidates for this fixture")
    st.caption("Candidates are experiment-queue items, not verified real failures.")
    st.json(report["failure_scout"])

with ablation_tab:
    st.subheader("SOUL metric delta after removing one mechanism")
    ablation_rows = [
        {"ablation": kind, **payload["soul_metric_delta_vs_unablated"]}
        for kind, payload in report["ablations"].items()
    ]
    st.dataframe(ablation_rows, width="stretch")
    selected_ablation = st.selectbox(
        "Inspect per-arm metrics", list(report["ablations"]), key="ablation_kind"
    )
    st.json(report["ablations"][selected_ablation])

with st.expander("CLI reproduction contract"):
    st.code(
        "team-soul demo-inspect --case " + case_id
        + " --fixture-dir fixtures --failure-report docs/FAILURE_REGISTER.json "
        + "--output-json docs/DEMO_INSPECTION_V02.json"
    )
    st.write(
        "CLI and Streamlit call the same `run_demo_inspection` function. "
        "The output JSON contains the experiment hash and all rendered data."
    )
