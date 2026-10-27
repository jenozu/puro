"""GetDocuments response fixtures: fully synthetic PDF bytes, no live API."""
from base64 import b64encode
from hashlib import sha256
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from puro.config import AppConfig
from puro.documents import DocumentProblem, LabelService
from puro.journal import ShipmentJournal
from puro.orders import ParsedImport


def pdf(pages=1):
    out = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=288, height=432)
    writer.write(out)
    return out.getvalue()


def env(tmp_path, pieces=1):
    j = ShipmentJournal(tmp_path / "puro.sqlite")
    row = {
        "doc_entry": "20", "doc_num": "2020", "card_code": "C20",
        "customer_name": "Fictional Warehouse",
    }
    j.store.import_orders(
        ParsedImport([row], [], sha256(b"fake").hexdigest()), "fake.xlsx",
    )
    # In the document tests we bypass UI and seed the confirmed carrier state
    # without ever contacting Purolator.
    with j.store._connect() as db:
        db.execute("UPDATE orders SET status='ready' WHERE doc_entry='20'")
    sid, _ = j.claim("20", environment="development", request_digest="testdigest123")
    j.mark_created(sid, "SHIP-PIN", [f"P{i}" for i in range(pieces)])
    config = AppConfig(labels_dir=tmp_path / "labels")
    return j, sid, config


class FakeCarrier:
    def __init__(self, *, status="Completed", data=None, document_type="DomesticBillOfLading"):
        self.status = status
        self.data = data
        self.document_type = document_type
        self.calls = []

    def call(self, service, operation, payload, **kwargs):
        self.calls.append((service, operation, payload))
        return {
            "Documents": {"Document": [{
                "PIN": {"Value": "SHIP-PIN"},
                "DocumentDetails": {"DocumentDetail": [{
                    "DocumentType": self.document_type,
                    "DocumentStatus": self.status,
                    "Data": self.data,
                    "URL": "",
                }]},
            }]}
        }


def test_save_real_parseable_pdf(tmp_path):
    j, sid, cfg = env(tmp_path)
    carrier = FakeCarrier(data=b64encode(pdf()).decode("ascii"))
    result = LabelService(cfg, j, carrier).get_and_archive(sid)
    assert result.status == "label_ready"
    assert result.path.is_file()
    assert len(PdfReader(str(result.path)).pages) == 1
    assert j.get(sid)["status"] == "label_ready"
    assert carrier.calls[0][0:2] == ("documents", "GetDocuments")
    criteria = carrier.calls[0][2]["DocumentCriterium"]["DocumentCriteria"][0]
    assert criteria["PIN"]["Value"] == "SHIP-PIN"


@pytest.mark.parametrize("status", ["Pending", "Queued", "Processing", "Error"])
def test_pending_or_error_never_marks_ready(tmp_path, status):
    j, sid, cfg = env(tmp_path)
    carrier = FakeCarrier(status=status, data=None)
    result = LabelService(cfg, j, carrier).get_and_archive(sid)
    assert result.status == "label_pending"
    assert not j.get(sid)["documents"]


def test_invalid_binary_is_never_ready(tmp_path):
    j, sid, cfg = env(tmp_path)
    carrier = FakeCarrier(data=b64encode(b"NOTPDF").decode("ascii"))
    with pytest.raises(DocumentProblem, match="non-PDF"):
        LabelService(cfg, j, carrier).get_and_archive(sid)
    assert j.get(sid)["status"] == "label_pending"


def test_piece_count_requires_enough_label_pages(tmp_path):
    j, sid, cfg = env(tmp_path, pieces=2)
    carrier = FakeCarrier(data=b64encode(pdf(pages=1)).decode("ascii"))
    with pytest.raises(DocumentProblem, match="fewer pages"):
        LabelService(cfg, j, carrier).get_and_archive(sid)
    assert j.get(sid)["status"] == "label_pending"


def test_previously_saved_label_does_not_retrieve_or_recreate(tmp_path):
    j, sid, cfg = env(tmp_path)
    first = FakeCarrier(data=b64encode(pdf()).decode("ascii"))
    label = LabelService(cfg, j, first).get_and_archive(sid)
    other = FakeCarrier(status="Error")
    again = LabelService(cfg, j, other).get_and_archive(sid)
    assert label.path == again.path
    assert again.status == "label_ready"
    assert other.calls == []
