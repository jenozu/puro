"""Adversarial and variant SAP spreadsheet tests."""
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import pytest

from puro.orders import ImportProblem, parse_export


def csv(row: dict) -> bytes:
    return pd.DataFrame([row]).to_csv(index=False).encode("utf-8")


def base():
    return {
        "DocEntry": "10", "DocNum": "000123", "CardCode": "C007",
        "CardName": "Fictional Recipient", "StreetS": "Station Road",
        "CityS": "Ottawa", "StateS": "ON", "ZipCodeS": "K1A0B1",
        "CountryS": "CA", "CANCELED": "N", "DocStatus": "O",
    }


def xlsx(orders, lines=None):
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as wr:
        pd.DataFrame(orders).to_excel(wr, sheet_name="Orders", index=False)
        if lines is not None:
            pd.DataFrame(lines).to_excel(wr, sheet_name="OrderLines", index=False)
    return out.getvalue()


def test_order_identity_and_leading_zero_document_number():
    result = parse_export(csv(base()), "sap.csv")
    assert result.orders[0]["doc_num"] == "000123"
    assert result.orders[0]["customer_name"] == "Fictional Recipient"


def test_missing_card_code_is_skipped():
    row = base()
    row["CardCode"] = ""
    result = parse_export(csv(row), "sap.csv")
    assert not result.orders
    assert any("missing DocNum" in issue for issue in result.issues)


def test_missing_card_code_header_rejected():
    row = base()
    del row["CardCode"]
    with pytest.raises(ImportProblem, match="card_code"):
        parse_export(csv(row), "sap.csv")


def test_orderlines_missing_ship_to_code_header_rejected():
    with pytest.raises(ImportProblem, match="OrderLines requires"):
        parse_export(xlsx([base()], [{"DocEntry": "10", "ItemCode": "A"}]),
                     "sap.xlsx")


def test_duplicate_source_identity_is_skipped():
    result = parse_export(xlsx([base(), base()]), "sap.xlsx")
    assert len(result.orders) == 1
    assert any("duplicate DocEntry" in issue for issue in result.issues)


def test_mismatched_orderlines_references_warn():
    result = parse_export(xlsx([base()], [
        {"DocEntry": "999", "LineShipToCode": "SHIPPING"}
    ]), "sap.xlsx")
    assert any("unimported DocEntry" in issue for issue in result.issues)


def test_xlsx_archive_bomb_guard():
    file = BytesIO()
    with ZipFile(file, "w") as zf:
        zf.writestr("xl/workbook.xml", b"x" * (81 * 1024 * 1024))
    with pytest.raises(ImportProblem, match="safe import limit"):
        parse_export(file.getvalue(), "sap.xlsx")


def test_reject_macro_excel():
    with pytest.raises(ImportProblem, match=".xlsx or .csv"):
        parse_export(csv(base()), "sap.xlsm")
