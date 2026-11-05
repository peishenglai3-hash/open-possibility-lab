from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_demo_renders_all_experiment_views_without_exceptions() -> None:
    script = Path(__file__).resolve().parents[1] / "app" / "demo.py"
    app = AppTest.from_file(script, default_timeout=30).run()

    assert len(app.exception) == 0
    assert len(app.button) == 1

    app.button[0].click().run()

    assert len(app.exception) == 0
    assert len(app.tabs) == 4
    assert len(app.dataframe) >= 2
    assert any("SOUL metric delta" in item.value for item in app.subheader)
