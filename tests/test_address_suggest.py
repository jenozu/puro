"""Pre-fill suggestions from SAP free-text addresses. Fictional addresses only."""
from io import BytesIO

import pandas as pd
import pytest

from puro.address_suggest import suggest_address
from puro.orders import parse_export
from puro.store import Store


def fields(s):
    return {k: s[k] for k in (
        "ship_to_name", "street_no", "street", "building", "address2",
        "city", "province", "postal_code", "country",
    )}


def test_multiline_regular_address_is_clean():
    s = suggest_address("55 Example Road Unit 4\nTestville ON K1A 0B1\nCANADA")
    assert fields(s) == {
        "ship_to_name": "", "street_no": "55", "street": "Example Road",
        "building": "4", "address2": "", "city": "Testville", "province": "ON",
        "postal_code": "K1A 0B1", "country": "CA",
    }
    assert s["notes"] == [] and s["layout"] == "multi-line"


def test_single_line_address_parses_but_warns_about_the_street_city_split():
    s = suggest_address("55 EXAMPLE RD UNIT 4 TESTVILLE ON K1A 0B1 CANADA")
    assert (s["street_no"], s["street"], s["building"], s["city"]) == (
        "55", "EXAMPLE RD", "4", "TESTVILLE")
    assert s["province"] == "ON" and s["postal_code"] == "K1A 0B1" and s["country"] == "CA"
    assert any("no line breaks" in n for n in s["notes"])


def test_unit_dash_number_prefix_and_unspaced_postal_code():
    s = suggest_address("4-55 EXAMPLE RD\nTESTVILLE ON K1A0B1")
    assert (s["building"], s["street_no"], s["street"]) == ("4", "55", "EXAMPLE RD")
    assert s["postal_code"] == "K1A 0B1"


def test_street_direction_stays_with_the_street():
    s = suggest_address("120 SAMPLE AVE E EXAMPLEVILLE ON M4N 1T1")
    assert (s["street"], s["city"]) == ("SAMPLE AVE E", "EXAMPLEVILLE")


def test_city_that_starts_with_st_is_not_split_into_the_street():
    s = suggest_address("9 KING ST ST CATHARINES ON L2R 3H8")
    assert (s["street"], s["city"]) == ("KING ST", "ST CATHARINES")


def test_full_province_name_is_converted():
    s = suggest_address("55 EXAMPLE RD\nTESTVILLE ONTARIO K1A 0B1")
    assert s["province"] == "ON"


def test_extra_line_goes_to_address_line_2_not_the_street():
    s = suggest_address("ATTN SHIPPING\n55 EXAMPLE RD\nTESTVILLE ON K1A 0B1")
    assert s["address2"] == "ATTN SHIPPING" and s["street"] == "EXAMPLE RD"


def test_cash_sale_single_line_splits_name_from_street():
    s = suggest_address("JANE SAMPLE 77 MAPLE CRES BARRIE ON L4N 6H1", cash_sale=True)
    assert (s["ship_to_name"], s["street_no"], s["street"], s["city"]) == (
        "JANE SAMPLE", "77", "MAPLE CRES", "BARRIE")


def test_cash_sale_multiline_name_line_is_detected():
    s = suggest_address("JANE SAMPLE\n77 MAPLE CRES\nBARRIE ON L4N 6H1\nCANADA", cash_sale=True)
    assert (s["ship_to_name"], s["street"], s["city"]) == (
        "JANE SAMPLE", "MAPLE CRES", "BARRIE")
    assert s["notes"] == []


def test_cash_sale_without_a_name_is_flagged():
    s = suggest_address("77 MAPLE CRES\nBARRIE ON L4N 6H1", cash_sale=True)
    assert s["ship_to_name"] == "" and any("recipient name" in n for n in s["notes"])


def test_po_box_is_flagged_and_has_no_street_number():
    s = suggest_address("PO BOX 123\nTESTVILLE ON K1A 0B1")
    assert s["street"] == "PO BOX 123" and s["street_no"] == ""
    assert any("PO box" in n for n in s["notes"])


@pytest.mark.parametrize("text", ["", "1 MAIN ST\nBUFFALO NY 14201\nUSA", "somewhere unclear"])
def test_unparseable_text_returns_no_guesses_and_a_note(text):
    s = suggest_address(text)
    assert not any(s[k] for k in ("street", "city", "postal_code", "province"))
    assert s["notes"]


def test_non_canadian_country_text_is_not_accepted():
    s = suggest_address("55 EXAMPLE RD\nTESTVILLE ON K1A 0B1\nFRANCE")
    assert s["country"] == "" and any("not Canada" in n for n in s["notes"])


def _workbook(rows):
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Sheet1", index=False)
    return bio.getvalue()


ROWS = [
    {"Internal Number": 201, "Document Number": 600001, "Customer/Vendor Code": "C200",
     "CustomerName": "Example Industries", "CustomerPO": "",
     "FullShipToAddress": "55 Example Road Unit 4\nTestville ON K1A 0B1\nCANADA"},
    {"Internal Number": 202, "Document Number": 600002, "Customer/Vendor Code": "CASH",
     "CustomerName": "Walk-in Customer", "CustomerPO": "",
     "FullShipToAddress": "JANE SAMPLE\n77 MAPLE CRES\nBARRIE ON L4N 6H1\nCANADA"},
]


def test_suggestions_are_attached_but_never_become_real_fields_or_approval(tmp_path):
    parsed = parse_export(_workbook(ROWS), "example.xlsx")
    regular, cash = parsed.orders
    assert regular["suggested_address"]["city"] == "Testville"
    # Real fields stay empty and the order still needs the operator's attestation.
    assert regular["street"] == "" and regular["city"] == ""
    assert regular["address_review_pending"]
    # A cash-sale customer is detected from the customer CODE even if the name differs.
    assert cash["cash_sale"] and cash["suggested_cash_recipient"] == "JANE SAMPLE"
    assert cash["ship_to_name"] == ""
    store = Store(tmp_path / "t.db")
    store.import_orders(parsed, "example.xlsx")
    with pytest.raises(ValueError):
        store.confirm_ready("201")
    assert store.get_order("201")["status"] == "needs_review"


def test_saving_a_draft_stops_further_prefill(tmp_path):
    store = Store(tmp_path / "t.db")
    store.import_orders(parse_export(_workbook(ROWS), "example.xlsx"), "example.xlsx")
    assert not store.get_order("201")["data"].get("address_draft_saved")
    store.save_draft("201", {"street": "Example Road"})
    data = store.get_order("201")["data"]
    assert data["address_draft_saved"] and data["address_review_pending"]


def test_review_form_shows_prefill_but_saves_nothing_until_operator_saves(monkeypatch, tmp_path):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PURO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUROLATOR_ENV", "development")
    monkeypatch.setenv("PURO_ENABLE_CARRIER", "false")
    store = Store(tmp_path / "puro.sqlite3")
    store.import_orders(parse_export(_workbook(ROWS), "example.xlsx"), "example.xlsx")

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("Review a shipment").run()
    app.selectbox[0].set_value("#600001 — Example Industries (DocEntry 201)").run()
    assert not app.exception
    by_key = {w.key: w.value for w in app.text_input}
    assert by_key["201_streetno"] == "55" and by_key["201_street"] == "Example Road"
    assert by_key["201_building"] == "4" and by_key["201_city"] == "Testville"
    assert by_key["201_province"] == "ON" and by_key["201_postal"] == "K1A 0B1"
    assert by_key["201_country"] == "CA"
    assert any("PRE-FILLED" in item.value for item in app.info)

    # Showing suggestions saved nothing and approved nothing.
    saved = store.get_order("201")
    assert saved["status"] == "needs_review"
    assert saved["data"]["street"] == "" and saved["data"]["address_review_pending"]
    ready_buttons = [b for b in app.button if "mark Ready" in str(b.label)]
    assert ready_buttons and all(b.disabled for b in ready_buttons)
