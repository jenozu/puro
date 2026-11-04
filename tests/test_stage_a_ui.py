"""Automated version of the Stage A browser checks (MANUAL_VALIDATION.md steps 2 and 5).

Runs the real Streamlit app in-process on fictional data with the carrier flag OFF.
This simulates the browser (AppTest); it is not a pixel-level check.
"""
import importlib.util
from pathlib import Path

from streamlit.testing.v1 import AppTest

from puro.orders import parse_export
from puro.store import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stage_a_check", ROOT / "scripts" / "stage_a_check.py")
stage_a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_a)


def page_text(app) -> str:
    parts = []
    for group in (app.markdown, app.warning, app.info, app.error, app.success, app.caption,
                  app.text_input, app.json):
        parts.extend(str(getattr(e, "value", "")) for e in group)
    parts.extend(str(df.value.to_dict()) for df in app.dataframe)
    parts.extend(str(o) for s in app.selectbox for o in s.options)
    parts.extend(str(o) for m in app.multiselect for o in m.options)
    return " ".join(parts)


def test_ship_to_shown_bill_to_never_and_no_create_control_with_carrier_off(monkeypatch, tmp_path):
    monkeypatch.setenv("PURO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUROLATOR_ENV", "development")
    monkeypatch.setenv("PURO_ENABLE_CARRIER", "false")
    monkeypatch.setenv("PURO_PRODUCTION_APPROVED", "false")
    store = Store(tmp_path / "puro.sqlite3")
    store.import_orders(parse_export(stage_a.build_legacy(), "synthetic.xlsx"), "synthetic.xlsx")

    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()
    assert not app.exception
    assert any("Offline preparation mode" in i.value for i in app.info)

    app.sidebar.radio[0].set_value("Orders").run()
    assert not app.exception
    cities = {c for df in app.dataframe for c in df.value.get("City", [])}
    assert "Ottawa" in cities
    assert "Toronto" not in page_text(app)

    app.multiselect[0].set_value(["9001", "9002"]).run()
    app.button[0].click().run()
    assert app.session_state["review_batch"] == ["9001", "9002"]

    app.sidebar.radio[0].set_value("Review a shipment").run()
    assert not app.exception
    pick = next(o for o in app.selectbox[0].options if "DocEntry 9001" in o)
    app.selectbox[0].set_value(pick).run()
    assert not app.exception
    review = page_text(app)
    assert "Warehouse Lane" in review and "Ottawa" in review
    assert "Toronto" not in review and "Accounts Avenue" not in review

    app.sidebar.radio[0].set_value("History & status").run()
    assert not app.exception
    assert any("review ONLY" in w.value for w in app.warning)
    for page in ("Orders", "Review a shipment", "History & status"):
        app.sidebar.radio[0].set_value(page).run()
        labels = [b.label for b in app.button]
        assert not any("Create" in label and "shipment" in label.lower() for label in labels), labels
