"""Carrier request construction tests: fictional identifiers and addresses."""
from decimal import Decimal

import pytest

from puro.config import AppConfig
from puro.payloads import (
    ShipmentValidationError, create_request, validate_request,
    request_digest,
)


def config():
    return AppConfig(
        sender_name="Example Shipping Warehouse",
        sender_street_no="10", sender_street="Example Street",
        sender_city="Ottawa", sender_province="ON",
        sender_postal_code="K1A0B1", sender_country="CA",
        sender_phone="6135550199",
        account_number="FAKEACCOUNT", api_key="FAKEKEY", api_password="FAKEPASS",
    )


def order():
    return {
        "doc_entry": "100", "doc_num": "430210", "card_code": "C100",
        "ship_to_name": "Ship To Receiving", "street_no": "50",
        "street": "Delivery Drive", "building": "Dock 2",
        "address2": "Rear entrance", "address3": "",
        "city": "Kingston", "province": "ON", "postal_code": "K7L1A1",
        "country": "CA", "phone": "6135550188",
        "service": "PurolatorGround", "payer": "Sender",
        "weight_lb": 5.0, "length_in": 10, "width_in": 8,
        "height_in": 6, "pieces": 1, "receiver_account": "",
        "StreetB": "13 Accounts Road", "CityB": "Toronto",
        "ZipCodeB": "M5V1A1",
    }


def test_created_shipment_payload_uses_correct_ship_to_and_schema():
    payload = create_request(order(), config(), pickup_type="PreScheduled")
    assert set(payload) == {"Shipment", "PrinterType"}
    assert payload["PrinterType"] == "Regular"
    shipment = payload["Shipment"]
    address = shipment["ReceiverInformation"]["Address"]
    assert address["StreetName"] == "Delivery Drive"
    assert address["Name"] == "Ship To Receiving"
    assert address["Suite"] == "Dock 2"
    assert address["City"] == "Kingston"
    assert address["PostalCode"] == "K7L 1A1"
    assert "13 Accounts Road" not in str(shipment)
    assert "Toronto" not in str(shipment)
    assert shipment["PaymentInformation"]["PaymentType"] == "Sender"
    assert shipment["PaymentInformation"]["RegisteredAccountNumber"] == "FAKEACCOUNT"
    assert shipment["PickupInformation"]["PickupType"] == "PreScheduled"
    assert shipment["PackageInformation"]["TotalPieces"] == 1
    assert shipment["PackageInformation"]["TotalWeight"]["Value"] == Decimal("5.0")


def test_multi_piece_weight_and_dimensions():
    p = order()
    p["pieces"] = 2
    parts = [
        {"weight_lb": "2.5", "length_in": 8, "width_in": 5, "height_in": 4},
        {"weight_lb": "3.1", "length_in": 12, "width_in": 8, "height_in": 6},
    ]
    payload = create_request(p, config(), pickup_type="DropOff", packages=parts)
    pkg = payload["Shipment"]["PackageInformation"]
    assert pkg["TotalPieces"] == 2
    assert pkg["TotalWeight"]["Value"] == Decimal("5.6")
    assert len(pkg["PiecesInformation"]["Piece"]) == 2
    assert pkg["PiecesInformation"]["Piece"][0]["Length"]["DimensionUnit"] == "in"


def test_receiver_account_billing_and_required_fields():
    row = order()
    row["payer"] = "Receiver"
    with pytest.raises(ShipmentValidationError, match="account"):
        create_request(row, config(), pickup_type="DropOff")
    row["receiver_account"] = "FAKERECEIVER"
    result = create_request(row, config(), pickup_type="DropOff")
    assert result["Shipment"]["PaymentInformation"]["BillingAccountNumber"] == "FAKERECEIVER"


def test_no_default_pickup_guess():
    with pytest.raises(ShipmentValidationError, match="Choose"):
        create_request(order(), config(), pickup_type="")


def test_missing_phone_blocks_preflight():
    row = order()
    row["phone"] = ""
    with pytest.raises(ShipmentValidationError, match="ten-digit"):
        create_request(row, config(), pickup_type="DropOff")


def test_invalid_weight_blocks():
    row = order()
    row["weight_lb"] = 0
    with pytest.raises(ShipmentValidationError, match="positive weight"):
        create_request(row, config(), pickup_type="DropOff")


def test_unsupported_service_is_blocked():
    row = order()
    row["service"] = "UnverifiedExpressFoo"
    with pytest.raises(ShipmentValidationError, match="carrier service"):
        create_request(row, config(), pickup_type="DropOff")


def test_line_destination_conflict_fails_closed():
    row = order()
    row["line_conflict"] = True
    with pytest.raises(ShipmentValidationError, match="Ship To conflict"):
        create_request(row, config(), pickup_type="DropOff")


def test_digest_does_not_expose_raw_address():
    payload = create_request(order(), config(), pickup_type="DropOff")
    digest = request_digest(payload)
    assert len(digest) == 64
    assert "Delivery Drive" not in digest
    altered = order()
    altered["street"] = "Other Dock"
    assert request_digest(create_request(altered, config(), pickup_type="DropOff")) != digest


def test_validate_request_is_only_shipment():
    result = validate_request(order(), config(), pickup_type="DropOff")
    assert list(result) == ["Shipment"]
