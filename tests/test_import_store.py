"""Safety-focused tests for the offline import/review increment.

All fixtures are fictional. Tests do not connect to SAP or Purolator.
"""
from __future__ import annotations

from io import BytesIO
import json

import pandas as pd
import pytest

from puro.orders import ImportProblem, parse_export, review_errors
from puro.store import Store


def workbook(orders: list[dict], lines: list[dict] | None = None) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame(orders).to_excel(writer, sheet_name="Orders", index=False)
        if lines is not None:
            pd.DataFrame(lines).to_excel(writer, sheet_name="OrderLines", index=False)
    return out.getvalue()


def valid_row() -> dict[str, str]:
    return {
        "DocEntry": "10001",
        "DocNum": "430210",
        "CardCode": "C000789",
        "CustomerName": "Example Repair Company",
        "ShipToCode": "WAREHOUSE",
        "StreetNoS": "12",
        "StreetS": "Warehouse Lane",
        "BuildingS": "Unit 4",
        "CityS": "Ottawa",
        "StateS": "ON",
        "ZipCodeS": "K1A 0B1",
        "CountryS": "CA",
        "StreetB": "88 Billing Road",
        "CityB": "Toronto",
        "ZipCodeB": "M5V 1A1",
        "ContactPhone": "6135550100",
        "DocStatus": "O",
        "CANCELED": "N",
    }


def ready_fields() -> dict:
    return {
        "weight_lb": 5.0,
        "length_in": 12.0,
        "width_in": 8.0,
        "height_in": 6.0,
        "pieces": 1,
        "service": "PurolatorGround",
        "payer": "Sender",
    }


def test_import_uses_only_shipping_address_not_bill_to():
    result = parse_export(workbook([valid_row()]), "orders.xlsx")
    order = result.orders[0]
    assert order["street"] == "Warehouse Lane"
    assert order["street_no"] == "12"
    assert order["city"] == "Ottawa"
    assert order["postal_code"] == "K1A 0B1"
    assert "Billing Road" not in json.dumps(order)
    assert "Toronto" not in json.dumps(order)


def test_no_bill_to_fallback_when_ship_to_missing():
    row = valid_row()
    row["StreetS"] = ""
    row["CityS"] = ""
    order = parse_export(workbook([row]), "orders.xlsx").orders[0]
    errs = review_errors({**order, **ready_fields()})
    assert "Missing Ship To street" in errs
    assert "Missing Ship To city" in errs
    assert order["street"] == ""


def test_workbook_with_only_bill_to_columns_is_rejected():
    row = valid_row()
    row = {k: v for k, v in row.items() if not k.endswith("S")}
    with pytest.raises(ImportProblem, match="No approved Ship To columns"):
        parse_export(workbook([row]), "orders.xlsx")


def test_line_level_destination_conflict():
    r = valid_row()
    data = parse_export(
        workbook([r], [
            {"DocEntry": "10001", "LineShipToCode": "WAREHOUSE"},
            {"DocEntry": "10001", "LineShipToCode": "SECONDARY"},
        ]),
        "orders.xlsx",
    )
    order = {**data.orders[0], **ready_fields()}
    assert order["line_conflict"]
    assert any("different Ship To" in e for e in review_errors(order))


def test_reimport_preserves_confirmed_order(tmp_path):
    st = Store(tmp_path / "orders.sqlite3")
    parsed = parse_export(workbook([valid_row()]), "orders.xlsx")
    assert st.import_orders(parsed, "orders.xlsx") == (1, 0)
    st.save_draft("10001", ready_fields())
    st.confirm_ready("10001")
    assert st.get_order("10001")["status"] == "ready"
    assert st.import_orders(parsed, "orders.xlsx") == (0, 1)
    reloaded = Store(tmp_path / "orders.sqlite3")
    assert reloaded.get_order("10001")["status"] == "ready"
    assert reloaded.get_order("10001")["data"]["weight_lb"] == 5.0



def test_original_ship_to_snapshot_remains_after_manual_edit(tmp_path):
    store = Store(tmp_path / "orders.sqlite3")
    parsed = parse_export(workbook([valid_row()]), "orders.xlsx")
    store.import_orders(parsed, "orders.xlsx")
    store.save_draft("10001", {"street": "Changed Avenue"})
    saved = store.get_order("10001")["data"]
    assert saved["street"] == "Changed Avenue"
    assert saved["source_address"]["street"] == "Warehouse Lane"
    assert saved["source_address"]["city"] == "Ottawa"

def test_address_override_revokes_review(tmp_path):
    store = Store(tmp_path / "orders.sqlite3")
    data = parse_export(workbook([valid_row()]), "orders.xlsx")
    store.import_orders(data, "orders.xlsx")
    store.save_draft("10001", ready_fields())
    store.confirm_ready("10001")
    store.save_draft("10001", {"city": "Kingston"})
    assert store.get_order("10001")["status"] == "needs_review"
    assert store.get_order("10001")["reviewed_at"] is None


def test_cannot_approve_missing_address_or_unmeasured_carton(tmp_path):
    store = Store(tmp_path / "orders.sqlite3")
    row = valid_row()
    row["StreetS"] = ""
    store.import_orders(parse_export(workbook([row]), "orders.xlsx"), "orders.xlsx")
    with pytest.raises(ValueError, match="Missing Ship To street"):
        store.confirm_ready("10001")


def test_receiver_account_is_required():
    order = {**parse_export(workbook([valid_row()]), "orders.xlsx").orders[0],
             **ready_fields(), "payer": "Receiver", "receiver_account": ""}
    assert any("Receiver billing" in e for e in review_errors(order))


def test_closed_and_cancelled_orders_are_not_imported():
    row = valid_row()
    row["DocStatus"] = "C"
    assert not parse_export(workbook([row]), "orders.xlsx").orders


def test_csv_import_preserves_string_identifiers():
    csv_data = pd.DataFrame([valid_row()]).to_csv(index=False).encode()
    data = parse_export(csv_data, "daily.csv")
    assert data.orders[0]["doc_entry"] == "10001"
    assert data.orders[0]["postal_code"] == "K1A 0B1"


def test_protected_order_cannot_be_reset_by_reimport(tmp_path):
    store = Store(tmp_path / "orders.sqlite3")
    parsed = parse_export(workbook([valid_row()]), "orders.xlsx")
    store.import_orders(parsed, "orders.xlsx")
    with store._connect() as db:
        db.execute("UPDATE orders SET status='creating' WHERE doc_entry='10001'")
    assert store.import_orders(parsed, "orders.xlsx") == (0, 1)
    with pytest.raises(ValueError, match="locked"):
        store.save_draft("10001", ready_fields())
    assert store.get_order("10001")["status"] == "creating"
