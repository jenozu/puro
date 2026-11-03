"""Stage B runner guards and flow with a FAKE carrier (no network)."""
import importlib.util
from base64 import b64encode
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from puro.carrier import CarrierOutcomeUnknown

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stage_b_dev_shipment.py"
spec = importlib.util.spec_from_file_location("stage_b", SCRIPT)
stage_b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_b)

RECIPIENT = ["--name", "Test Receiver", "--street-no", "20", "--street", "Delivery Road",
             "--city", "Kingston", "--province", "ON", "--postal", "K7L1A1", "--phone", "613-555-0199"]


def pdf_b64() -> str:
    w = PdfWriter(); w.add_blank_page(72, 72)
    buf = BytesIO(); w.write(buf)
    return b64encode(buf.getvalue()).decode()


class Fake:
    def __init__(self, valid=True, fail_create=False):
        self.valid, self.fail_create, self.ops = valid, fail_create, []

    def call(self, service, operation, payload, **kw):
        self.ops.append(operation)
        if operation == "ValidateShipment":
            return {"ResponseInformation": {"Errors": {"Error": []}}, "ValidShipment": self.valid}
        if operation == "CreateShipment":
            if self.fail_create:
                raise CarrierOutcomeUnknown("simulated timeout")
            return {"ResponseInformation": {"Errors": {"Error": []}}, "ShipmentPIN": {"Value": "DEVPIN1"},
                    "PiecePINs": {"PIN": [{"Value": "DEVPIECE1"}]}}
        return {"Documents": {"Document": [{"DocumentDetails": {"DocumentDetail": [
            {"DocumentType": "DomesticBillOfLading", "DocumentStatus": "Completed", "Data": pdf_b64()}]}}]}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {"PUROLATOR_ENV": "development", "PURO_ENABLE_CARRIER": "true",
                 "PUROLATOR_API_KEY": "FAKEKEY", "PUROLATOR_API_PASSWORD": "FAKEPASS",
                 "PUROLATOR_ACCOUNT_NUMBER": "0000000000", "DEFAULT_SENDER_NAME": "Example Warehouse",
                 "DEFAULT_SENDER_STREET": "Example Avenue", "DEFAULT_SENDER_CITY": "Ottawa",
                 "DEFAULT_SENDER_PROVINCE": "ON", "DEFAULT_SENDER_POSTAL_CODE": "K1A0B1",
                 "DEFAULT_SENDER_PHONE": "6135550188"}.items():
        monkeypatch.setenv(k, v)
    return ["--data-dir", str(tmp_path / "d"), "--labels-dir", str(tmp_path / "l")]


def run(mode, env, carrier, extra=()):
    return stage_b.main([*mode, *RECIPIENT, *env, *extra], carrier_factory=lambda c: carrier)


def test_refuses_production_and_makes_no_calls(monkeypatch, env):
    monkeypatch.setenv("PUROLATOR_ENV", "production")
    fake = Fake()
    assert run(["--validate-only"], env, fake) == 2 and fake.ops == []


def test_refuses_when_carrier_flag_off_and_when_production_approved(monkeypatch, env):
    fake = Fake()
    monkeypatch.setenv("PURO_ENABLE_CARRIER", "false")
    assert run(["--validate-only"], env, fake) == 2
    monkeypatch.setenv("PURO_ENABLE_CARRIER", "true")
    monkeypatch.setenv("PURO_PRODUCTION_APPROVED", "true")
    assert run(["--validate-only"], env, fake) == 2 and fake.ops == []


def test_ship_requires_explicit_approval_flag(env):
    fake = Fake()
    assert run(["--ship"], env, fake) == 2 and fake.ops == []


def test_validate_only_never_creates(env):
    fake = Fake()
    assert run(["--validate-only"], env, fake) == 0 and fake.ops == ["ValidateShipment"]
    bad = Fake(valid=False)
    assert run(["--validate-only"], env, bad) == 1 and bad.ops == ["ValidateShipment"]


def test_one_shipment_label_saved_and_second_attempt_refused(env, tmp_path, capsys):
    fake = Fake()
    assert run(["--ship"], env, fake, ["--approve-development-shipment"]) == 0
    assert fake.ops == ["ValidateShipment", "CreateShipment", "GetDocuments"]
    assert list((tmp_path / "l").rglob("*.pdf"))
    assert "STAGE B SHIPMENT OK" in capsys.readouterr().out
    again = Fake()
    assert run(["--ship"], env, again, ["--approve-development-shipment"]) == 3
    assert again.ops == []


def test_timeout_is_reconciliation_and_never_retried(env):
    fake = Fake(fail_create=True)
    assert run(["--ship"], env, fake, ["--approve-development-shipment"]) == 4
    assert fake.ops.count("CreateShipment") == 1
    again = Fake()
    assert run(["--ship"], env, again, ["--approve-development-shipment"]) == 3
    assert again.ops == []
