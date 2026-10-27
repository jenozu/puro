"""Batch safety and local export behavior — no network or real carrier data."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest

from puro.batch import BatchRunner, history, label_zip, shipment_history_csv
from puro.config import AppConfig
from puro.documents import LabelResult
from puro.journal import ShipmentJournal
from puro.orders import ParsedImport
from puro.payloads import ShipmentValidationError
from puro.shipping import ShipResult


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def ship(self, doc_entry, **kwargs):
        self.calls.append(doc_entry)
        if doc_entry == "2":
            raise ShipmentValidationError("invalid address")
        return ShipResult("ship-" + doc_entry, "created", "PIN-" + doc_entry)


class FakeLabeler:
    def __init__(self):
        self.calls = []

    def get_and_archive(self, shipment_id, **kwargs):
        self.calls.append(shipment_id)
        return LabelResult(shipment_id, "label_ready", message="saved")


def test_partial_batch_failure_keeps_other_success():
    shipping = FakeCoordinator()
    labels = FakeLabeler()
    results = BatchRunner(shipping, labels).run(["1", "2", "3"], pickup_type="DropOff")
    assert [r.state for r in results] == [
        "label_ready", "needs_review", "label_ready"
    ]
    assert shipping.calls == ["1", "2", "3"]
    assert labels.calls == ["ship-1", "ship-3"]


def test_repeated_batch_identity_is_blocked_before_any_shipping():
    shipping = FakeCoordinator()
    with pytest.raises(ValueError, match="Repeated"):
        BatchRunner(shipping, FakeLabeler()).run(["1", "1"], pickup_type="DropOff")
    assert shipping.calls == []


def test_batch_requires_nonempty_input():
    with pytest.raises(ValueError):
        BatchRunner(FakeCoordinator(), FakeLabeler()).run([], pickup_type="DropOff")


def prepared_journal(tmp_path):
    j = ShipmentJournal(tmp_path / "history.sqlite")
    row = {"doc_entry": "1", "doc_num": "1020", "card_code": "C1",
           "customer_name": "Example Customer"}
    j.store.import_orders(ParsedImport([row], [], sha256(b"fakedata").hexdigest()),
                          "fake.xlsx")
    with j.store._connect() as db:
        db.execute("UPDATE orders SET status='ready' WHERE doc_entry='1'")
    sid, _ = j.claim("1", environment="development", request_digest="12345678")
    j.mark_created(sid, "PIN-1", ["PIN-1"])
    return j, sid


def test_history_and_label_zip_use_only_saved_verified_documents(tmp_path):
    j, sid = prepared_journal(tmp_path)
    # Synthetic PDF stored by the journal; not obtained from carrier.
    j.archive_pdf(sid, b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                  tmp_path / "labels")
    rows = history(ShipmentJournal(tmp_path / "history.sqlite"))
    assert rows[0]["status"] == "label_ready"
    report = shipment_history_csv(j)
    assert b"PIN-1" in report
    archive = BytesIO(label_zip(j))
    with ZipFile(archive) as zipfile:
        names = zipfile.namelist()
        assert len(names) == 1
        assert names[0].endswith(".pdf")


def test_zip_excludes_unready_shipments(tmp_path):
    j, sid = prepared_journal(tmp_path)
    with ZipFile(BytesIO(label_zip(j))) as zipfile:
        assert zipfile.namelist() == []


def test_carrier_execution_flags_default_off(monkeypatch):
    monkeypatch.delenv("PURO_ENABLE_CARRIER", raising=False)
    monkeypatch.delenv("PURO_PRODUCTION_APPROVED", raising=False)
    assert AppConfig.from_environment(dotenv=False).carrier_enabled is False
    assert AppConfig.from_environment(dotenv=False).production_authorized is False
