"""End-to-end carrier-mocked shipping acceptance for SAP Ship To, not Bill To."""
from base64 import b64encode
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import pytest
from pypdf import PdfReader, PdfWriter

from puro.config import AppConfig
from puro.documents import LabelService
from puro.journal import ShipmentJournal, ShipmentStateError
from puro.orders import parse_export
from puro.shipping import ShippingCoordinator
from puro.batch import label_zip, shipment_history_csv


def input_workbook() -> bytes:
    order = {
        "DocEntry": "501", "DocNum": "430501",
        "CardCode": "C0501", "CustomerName": "Accounts Company",
        "ShipToName": "Warehouse Receiving",
        "StreetNoS": "10", "StreetS": "Shipping Drive",
        "BuildingS": "Unit 2", "Address2S": "Rear Dock",
        "CityS": "Ottawa", "StateS": "ON",
        "ZipCodeS": "K1A 0B1", "CountryS": "CA",
        "ContactPhone": "6135550191",
        "StreetB": "99 Billing Avenue", "CityB": "Toronto",
        "ZipCodeB": "M5V1A1",
        "DocStatus": "O", "CANCELED": "N",
    }
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as wr:
        pd.DataFrame([order]).to_excel(wr, sheet_name="Orders", index=False)
    return output.getvalue()


def test_complete_offline_shipping_acceptance_path(tmp_path):
    pdf_buf = BytesIO()
    pw = PdfWriter()
    pw.add_blank_page(width=288, height=432)
    pw.write(pdf_buf)
    label_bytes = pdf_buf.getvalue()

    class MockCarrier:
        def __init__(self):
            self.calls = []

        def call(self, service, operation, payload, **kwargs):
            self.calls.append((service, operation, payload))
            if operation in {"ValidateShipment", "CreateShipment"}:
                receiver = payload["Shipment"]["ReceiverInformation"]["Address"]
                assert receiver["StreetName"] == "Shipping Drive"
                assert receiver["City"] == "Ottawa"
                assert "99 Billing Avenue" not in str(payload)
                assert "Toronto" not in str(payload)
            if operation == "ValidateShipment":
                return {"ResponseInformation": {"Errors": {"Error": []}}}
            if operation == "CreateShipment":
                return {
                    "ResponseInformation": {"Errors": {"Error": []}},
                    "ShipmentPIN": {"Value": "SHIP-FICTIONAL"},
                    "PiecePINs": {"PIN": [{"Value": "PIECE-FICTIONAL"}]},
                }
            if operation == "GetDocuments":
                assert payload["Synchronous"] is True
                return {"Documents": {"Document": [{
                    "PIN": {"Value": "SHIP-FICTIONAL"},
                    "DocumentDetails": {"DocumentDetail": [{
                        "DocumentType": "DomesticBillOfLading",
                        "DocumentStatus": "Completed",
                        "Data": b64encode(label_bytes).decode("ascii"),
                    }]},
                }]}}
            raise AssertionError(f"Unexpected operation: {operation}")

    cfg = AppConfig(
        labels_dir=tmp_path / "labels", account_number="FAKE-ACCOUNT",
        sender_name="Example Warehouse", sender_street_no="2",
        sender_street="Example Road", sender_city="Ottawa",
        sender_province="ON", sender_postal_code="K1A0B1",
        sender_country="CA", sender_phone="6135550192",
        api_key="FAKE-KEY", api_password="FAKE-PASSWORD",
    )
    parsed = parse_export(input_workbook(), "example.xlsx")
    assert len(parsed.orders) == 1
    journal = ShipmentJournal(tmp_path / "journal.sqlite")
    assert journal.store.import_orders(parsed, "example.xlsx") == (1, 0)
    journal.store.save_draft("501", {
        "weight_lb": 2.5, "length_in": 10, "width_in": 8, "height_in": 6,
        "pieces": 1, "payer": "Sender",
    })
    journal.store.confirm_ready("501")

    carrier = MockCarrier()
    co = ShippingCoordinator(cfg, journal, carrier)
    result = co.ship("501", pickup_type="PreScheduled")
    assert result.state == "created"
    assert result.shipment_pin == "SHIP-FICTIONAL"
    label = LabelService(cfg, journal, carrier).get_and_archive(result.shipment_id)
    assert label.status == "label_ready"
    assert label.path.is_file()
    assert len(PdfReader(str(label.path)).pages) == 1
    assert journal.get(result.shipment_id)["pieces"][0]["piece_pin"] == "PIECE-FICTIONAL"
    assert "SHIP-FICTIONAL" in shipment_history_csv(journal).decode("utf-8")
    with ZipFile(BytesIO(label_zip(journal))) as archive:
        assert len(archive.namelist()) == 1

    # Reimport and restart cannot accidentally reopen already shipped order.
    assert journal.store.import_orders(parsed, "example.xlsx") == (0, 1)
    assert ShipmentJournal(tmp_path / "journal.sqlite").get(result.shipment_id)["status"] == "label_ready"
    with pytest.raises(ShipmentStateError):
        co.ship("501", pickup_type="PreScheduled")
    assert [op for _, op, _ in carrier.calls].count("CreateShipment") == 1
