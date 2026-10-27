"""Streamlit order staging smoke tests — fictional data, no carrier connection."""
from hashlib import sha256
from pathlib import Path

from streamlit.testing.v1 import AppTest

from puro.orders import ParsedImport
from puro.store import Store


def test_select_review_batch_without_shipping(monkeypatch, tmp_path):
    monkeypatch.setenv("PURO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUROLATOR_ENV", "development")
    data = {
        "doc_entry": "5", "doc_num": "5001", "card_code": "C5",
        "customer_name": "Example Warehouse",
        "ship_to_name": "Example Warehouse",
        "street": "Sample Road", "street_no": "1", "city": "Ottawa",
        "province": "ON", "postal_code": "K1A0B1", "country": "CA",
        "weight_lb": 2.0, "length_in": 8.0, "width_in": 8.0,
        "height_in": 8.0, "pieces": 1, "payer": "Sender",
        "service": "PurolatorGround", "line_conflict": False,
    }
    store = Store(tmp_path / "puro.sqlite3")
    store.import_orders(ParsedImport([data], [], sha256(b"fakedata").hexdigest()),
                        "fictional.xlsx")

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"),
        default_timeout=20,
    ).run()
    assert not app.exception

    app.sidebar.radio[0].set_value("Orders").run()
    assert not app.exception
    app.multiselect[0].set_value(["5"]).run()
    app.button[0].click().run()
    assert app.session_state["review_batch"] == ["5"]

    app.sidebar.radio[0].set_value("History & status").run()
    assert not app.exception
    assert any("review ONLY" in item.value for item in app.warning)
