"""Verify the Streamlit review-only UI starts without SQL or carrier access."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_app_starts_in_offline_review_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PURO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUROLATOR_ENV", "development")
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=20).run()
    assert len(at.exception) == 0
    assert any("Puro" in title.value for title in at.title)
    assert any("no Purolator API calls" in c.value for c in at.caption)
