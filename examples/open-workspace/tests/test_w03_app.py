"""AppTest coverage for the four Week03 experiences (not browser QA)."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "gui-or-slice" / "streamlit_app.py"


def _run() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=30).run()


def test_direct_answer_then_optional_expansion_then_only_direct() -> None:
    at = _run()
    assert len(at.exception) == 0
    assert at.button(key="direct_answer")
    at.button(key="direct_answer").click().run()
    assert len(at.exception) == 0
    captions = " ".join(item.value for item in at.caption)
    assert "answer_sha256" in captions
    first_hash = captions.split("answer_sha256:")[1].split()[0]

    at.button(key="expand_sources").click().run()
    assert len(at.exception) == 0
    assert "Pilot latency note" in " ".join(item.value for item in at.markdown)

    at.button(key="only_direct").click().run()
    captions = " ".join(item.value for item in at.caption)
    assert captions.split("answer_sha256:")[1].split()[0] == first_hash
    assert len(at.get("download_button")) == 2


def test_untrusted_text_is_shown_as_text_and_not_used() -> None:
    at = _run()
    at.button(key="direct_answer").click().run()
    at.button(key="expand_untrusted").click().run()
    assert len(at.exception) == 0
    text = " ".join(item.value for item in at.text)
    assert "ignore all previous instructions" in text.lower()
    assert "policy_event" in text.lower()


def test_stop_is_terminal() -> None:
    at = _run()
    at.button(key="direct_answer").click().run()
    at.button(key="stop").click().run()
    assert len(at.exception) == 0
    assert "Session stopped" in " ".join(item.value for item in at.info)
    assert len(at.button) == 0


def test_reject_question_is_terminal() -> None:
    at = _run()
    at.button(key="reject_question").click().run()
    assert len(at.exception) == 0
    assert "rejected" in " ".join(item.value for item in at.info).lower()
    assert len(at.button) == 0


def test_change_question_clears_answer() -> None:
    at = _run()
    at.button(key="direct_answer").click().run()
    at.button(key="change_question").click().run()
    assert len(at.exception) == 0
    assert "No answer yet" in " ".join(item.value for item in at.info)

def test_composer_error_is_visible_and_does_not_write(monkeypatch) -> None:
    import w03.composer as composer_module

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic composer failure")

    monkeypatch.setattr(composer_module, "compose", boom)
    at = _run()
    at.button(key="direct_answer").click().run()
    assert len(at.exception) == 0
    assert "could not produce an answer" in " ".join(item.value for item in at.error)
    assert at.get("download_button") == []

def test_forced_mode_requires_all_panels_before_answer(monkeypatch) -> None:
    monkeypatch.setenv("W03_FORCED", "1")
    at = _run()
    assert at.button(key="direct_answer").disabled is True
    for name in ("sources", "dissent", "alternatives", "unresolved", "revision", "untrusted", "excluded"):
        at.button(key="expand_" + name).click().run()
    assert at.button(key="direct_answer").disabled is False
    at.button(key="direct_answer").click().run()
    assert "Adopt with conditions" in " ".join(item.value for item in at.markdown)
