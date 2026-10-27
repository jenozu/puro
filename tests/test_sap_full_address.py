"""Synthetic workbook matching the six SAP copied-table headers; never customer records."""
from io import BytesIO
import pandas as pd
import pytest

from puro.orders import parse_export, review_errors
from puro.store import Store


def workbook():
    rows = [
        {"Internal Number": 101, "Document Number": 500001,
         "Customer/Vendor Code": "C100", "CustomerName": "Example Industries",
         "CustomerPO": "", "FullShipToAddress": "100 Sample Road Unit 4 Ottawa ON K1A 0B1 CANADA"},
        {"Internal Number": 102, "Document Number": 500002,
         "Customer/Vendor Code": "CASH", "CustomerName": "CASH SALE",
         "CustomerPO": "", "FullShipToAddress": "Acme Sample Repair 140 Fictional St Russell ON K0A 1W0 Canada"},
    ]
    bio = BytesIO()
    with pd.ExcelWriter(bio,engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer,sheet_name="Sheet1",index=False)
    return bio.getvalue()


def test_raw_copy_table_import_preserves_complete_address():
    parsed = parse_export(workbook(),"example.xlsx")
    assert len(parsed.orders) == 2
    assert parsed.orders[0]["ship_to_name"] == "Example Industries"
    assert parsed.orders[0]["full_ship_to_address"].startswith("100 Sample")
    assert parsed.orders[0]["street"] == ""
    assert parsed.orders[0]["city"] == ""
    assert parsed.orders[0]["address_review_pending"]
    assert parsed.orders[1]["cash_sale"]
    assert parsed.orders[1]["ship_to_name"] == ""
    assert parsed.orders[1]["full_ship_to_address"].startswith("Acme Sample Repair")


def test_address_must_be_verified_before_ready_even_if_fields_complete(tmp_path):
    store=Store(tmp_path/"test.db")
    parsed=parse_export(workbook(),"example.xlsx")
    store.import_orders(parsed,"example.xlsx")
    store.save_draft("101",{
        "ship_to_name":"Example Industries", "street_no":"100",
        "street":"Sample Road","building":"Unit 4","city":"Ottawa",
        "province":"ON","postal_code":"K1A0B1","country":"CA",
        "weight_lb":2,"length_in":6,"width_in":6,"height_in":6,
    })
    with pytest.raises(ValueError,match="Confirm the parsed"):
        store.confirm_ready("101")
    store.confirm_address_review("101")
    store.confirm_ready("101")
    assert store.get_order("101")["status"]=="ready"
    store.save_draft("101",{"street":"Other Street"})
    assert store.get_order("101")["data"]["address_review_pending"]
    with pytest.raises(ValueError,match="Confirm the parsed"):
        store.confirm_ready("101")


def test_cash_sale_requires_explicit_recipient_and_address_confirmation(tmp_path):
    store=Store(tmp_path/"test.db")
    store.import_orders(parse_export(workbook(),"example.xlsx"),"example.xlsx")
    with pytest.raises(ValueError,match="Missing Ship To recipient"):
        store.confirm_address_review("102")
    assert store.get_order("102")["data"]["source_address"] == {
        "full_ship_to_address":"Acme Sample Repair 140 Fictional St Russell ON K0A 1W0 Canada"
    }
