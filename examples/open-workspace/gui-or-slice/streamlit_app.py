"""Week03 Open Workspace — Streamlit slice (synthetic, read-only, no authority writes).

Run via w03/launcher.py so bind/telemetry/error/toolbar settings are explicit. The app uses only
the synthetic project and the deterministic composer; it imports no team_soul control-plane module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for candidate in (ROOT,):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from w03.composer import EXPANSIONS, compose, expansion, reject_question, refusal_response  # noqa: E402
from w03.export import export_json, export_markdown  # noqa: E402
from w03.project import load_project  # noqa: E402

st.set_page_config(page_title="Team Soul Week03 — synthetic open workspace", layout="wide")

PROJECT = load_project()
FORCED = os.environ.get("W03_FORCED") == "1"
STATE = st.session_state
STATE.setdefault("task_id", "T1")
STATE.setdefault("answer", None)
STATE.setdefault("expanded", set())
STATE.setdefault("stopped", False)
STATE.setdefault("rejected", False)

TOGGLES = (
    ("sources", "展开来源"),
    ("dissent", "展开异议"),
    ("alternatives", "展开替代"),
    ("unresolved", "展开缺失"),
    ("revision", "展开修订"),
    ("untrusted", "展开未授信文本"),
    ("excluded", "展开被排除项"),
)


def render_toggles() -> None:
    cols = st.columns(3)
    for index, (name, label) in enumerate(TOGGLES):
        if cols[index % 3].button(label, key="expand_" + name):
            expanded = set(STATE["expanded"])
            expanded.add(name)
            STATE["expanded"] = expanded


def render_panels(result, expanded: set[str]) -> None:
    panels = expansion(result)
    for name in EXPANSIONS:
        if name not in expanded:
            continue
        st.markdown(f"### {name}")
        if name == "untrusted":
            st.warning("Untrusted external text. It is displayed as data and never executed as instruction.")
            for item in panels[name]:
                st.text(item["id"] + " " + item["title"] + " [" + item["status"] + "]")
                st.text(item["text"])
            continue
        if name == "excluded":
            st.caption("Excluded from the answer because the scope does not match.")
        for item in panels[name]:
            st.write(f"{item['id']} · {item['title']} · {item['status']}")
            st.text(item["text"])


st.title("Team Soul / Soul Lite — Week03 synthetic open workspace")
st.caption("Synthetic project · deterministic local responder · no model, no network, no authority writes")

if STATE["stopped"]:
    payload = refusal_response(STATE["task_id"])
    st.info(payload["message"])
    st.caption("status: STOPPED · nothing is recorded · you may close the page")
    st.stop()

if STATE["rejected"]:
    payload = reject_question(STATE["task_id"])
    st.info(payload["message"])
    st.caption("status: QUESTION_REJECTED · nothing is recorded · you may close the page")
    st.stop()

task_id = st.selectbox(
    "Current task",
    list(PROJECT.tasks),
    index=list(PROJECT.tasks).index(STATE["task_id"]),
    format_func=lambda key: PROJECT.tasks[key].label,
)
if task_id != STATE["task_id"]:
    STATE["task_id"] = task_id
    STATE["answer"] = None
    STATE["expanded"] = set()

st.caption("Simple path: one action for the direct answer. Expansion is optional, never required.")
if FORCED:
    st.caption("Forced-exploration negative control: the direct answer unlocks only after all panels are opened.")
    try:
        preview = compose(PROJECT, STATE["task_id"])
    except Exception:
        preview = None
    if preview is not None:
        render_toggles()
        render_panels(preview, set(STATE["expanded"]))

direct_disabled = FORCED and set(STATE["expanded"]) != set(EXPANSIONS)
if st.button("直接回答", key="direct_answer", disabled=direct_disabled):
    try:
        STATE["answer"] = compose(PROJECT, STATE["task_id"])
    except Exception:
        STATE["answer"] = None
        st.error("The local composer could not produce an answer. No answer is shown; nothing was written.")
    if not FORCED:
        STATE["expanded"] = set()

if st.button("换个问题", key="change_question"):
    keys = list(PROJECT.tasks)
    index = (keys.index(STATE["task_id"]) + 1) % len(keys)
    STATE["task_id"] = keys[index]
    STATE["answer"] = None
    STATE["expanded"] = set()

if st.button("完全拒绝当前问题", key="reject_question"):
    STATE["rejected"] = True
    st.rerun()

if st.button("停止", key="stop"):
    STATE["stopped"] = True
    STATE["answer"] = None
    st.rerun()

answer = STATE["answer"]
if answer is None:
    st.info("No answer yet. The direct answer remains one action away.")
else:
    st.subheader("Answer")
    st.write(answer.answer)
    st.caption(f"status: {answer.status} · answer_sha256: {answer.answer_hash}")

    if not FORCED:
        if st.button("只保留直接答案", key="only_direct"):
            STATE["expanded"] = set()
        render_toggles()
        render_panels(answer, set(STATE["expanded"]))

    md = export_markdown(PROJECT.project_id, answer)
    js = export_json(PROJECT.project_id, answer)
    left, right = st.columns(2)
    left.download_button("导出 Markdown", data=md, file_name="week03-export.md", mime="text/markdown")
    right.download_button("导出 JSON", data=js, file_name="week03-export.json", mime="application/json")
    st.caption("Export is allowlisted, synthetic, and readable without Team Soul.")
