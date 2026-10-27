"""Shipping coordinator tests are strictly mocked and use fictional data."""
from hashlib import sha256

import pytest

from puro.carrier import CarrierOutcomeUnknown
from puro.config import AppConfig
from puro.journal import ShipmentJournal, ShipmentStateError
from puro.orders import ParsedImport
from puro.shipping import ShippingCoordinator
from puro.payloads import ShipmentValidationError


def config(environment="development"):
    return AppConfig(
        environment=environment, api_key="FAKEKEY", api_password="FAKEPASS",
        account_number="FAKEACCOUNT",
        sender_name="Example Warehouse", sender_street_no="10",
        sender_street="Example Avenue", sender_city="Ottawa",
        sender_province="ON", sender_postal_code="K1A0B1",
        sender_phone="6135550188",
    )


def order():
    return {
        "doc_entry": "100", "doc_num": "4321", "card_code": "C100",
        "customer_name": "Fictional Business",
        "ship_to_name": "Warehouse Receiving", "street_no": "20",
        "street": "Delivery Road", "building": "",
        "address2": "", "address3": "", "city": "Kingston",
        "province": "ON", "postal_code": "K7L1A1", "country": "CA",
        "phone": "6135550199", "service": "PurolatorGround",
        "payer": "Sender", "receiver_account": "", "pieces": 1,
        "weight_lb": 1, "length_in": 4, "width_in": 4, "height_in": 4,
        "line_conflict": False,
    }


def journal(tmp_path):
    j = ShipmentJournal(tmp_path / "puro.sqlite")
    j.store.import_orders(
        ParsedImport([order()], [], sha256(b"fakedata").hexdigest()),
        "fictional.xlsx",
    )
    j.store.confirm_ready("100")
    return j


class FakeCarrier:
    def __init__(self, *, validate=None, create=None, fail_create=False):
        self.validate = validate if validate is not None else {
            "ResponseInformation": {"Errors": {"Error": []}}
        }
        self.create = create if create is not None else {
            "ResponseInformation": {"Errors": {"Error": []}},
            "ShipmentPIN": {"Value": "SHIP-PIN-001"},
            "PiecePINs": {"PIN": [{"Value": "PIECE-PIN-001"}]},
        }
        self.fail_create = fail_create
        self.operations = []

    def call(self, service, operation, payload, **kwargs):
        self.operations.append(operation)
        if operation == "ValidateShipment":
            return self.validate
        if self.fail_create:
            raise CarrierOutcomeUnknown("simulated timeout")
        return self.create


def test_success_saves_ids_and_avoids_duplicate_submission(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier()
    coordinator = ShippingCoordinator(config(), j, carrier)
    result = coordinator.ship("100", pickup_type="DropOff")
    assert result.state == "created"
    assert carrier.operations == ["ValidateShipment", "CreateShipment"]
    assert j.get(result.shipment_id)["pieces"][0]["piece_pin"] == "PIECE-PIN-001"
    with pytest.raises(ShipmentStateError):
        coordinator.ship("100", pickup_type="DropOff")
    assert carrier.operations.count("CreateShipment") == 1


def test_timeout_needs_reconciliation_and_no_retry(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier(fail_create=True)
    coordinator = ShippingCoordinator(config(), j, carrier)
    r = coordinator.ship("100", pickup_type="DropOff")
    assert r.state == "needs_reconciliation"
    assert j.get(r.shipment_id)["status"] == "needs_reconciliation"
    with pytest.raises(ShipmentStateError):
        coordinator.ship("100", pickup_type="DropOff")
    assert carrier.operations.count("CreateShipment") == 1


def test_validation_failure_does_not_claim_or_create(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier(validate={
        "ResponseInformation": {"Errors": {"Error": [{"Code": "BADZIP"}]}}
    })
    with pytest.raises(ShipmentValidationError, match="BADZIP"):
        ShippingCoordinator(config(), j, carrier).ship("100", pickup_type="DropOff")
    assert carrier.operations == ["ValidateShipment"]
    assert j.store.get_order("100")["status"] == "ready"


def test_explicit_carrier_rejection_is_persisted(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier(create={
        "ResponseInformation": {"Errors": {"Error": [{"Code": "PAYMENT_INVALID"}]}}
    })
    r = ShippingCoordinator(config(), j, carrier).ship("100", pickup_type="DropOff")
    assert r.state == "failed"
    assert j.get(r.shipment_id)["last_error"] == "PAYMENT_INVALID"


def test_incomplete_multi_piece_response_needs_reconciliation(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier()
    parts = [
        {"weight_lb": 2, "length_in": 5, "width_in": 6, "height_in": 7},
        {"weight_lb": 3, "length_in": 8, "width_in": 9, "height_in": 10},
    ]
    r = ShippingCoordinator(config(), j, carrier).ship(
        "100", pickup_type="DropOff", packages=parts,
    )
    assert r.state == "needs_reconciliation"
    assert j.get(r.shipment_id)["shipment_pin"] == "SHIP-PIN-001"


def test_production_requires_explicit_confirmation_before_any_api_call(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier()
    with pytest.raises(ShipmentValidationError, match="confirmation"):
        ShippingCoordinator(config("production"), j, carrier).ship(
            "100", pickup_type="DropOff", production_approved=False,
        )
    assert carrier.operations == []


def test_unknown_response_is_not_false_success(tmp_path):
    j = journal(tmp_path)
    carrier = FakeCarrier(create=None)
    carrier.create = None
    r = ShippingCoordinator(config(), j, carrier).ship("100", pickup_type="DropOff")
    assert r.state == "needs_reconciliation"
