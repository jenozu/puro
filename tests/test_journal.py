"""Carrier journal transaction tests: fictional orders; no Purolator connections."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import sqlite3

import pandas as pd
import pytest

from puro.journal import ShipmentJournal, ShipmentStateError
from puro.orders import parse_export
from puro.store import Store


def ready_order(path):
    row = {
        "DocEntry": "42", "DocNum": "10200", "CustomerName": "Fictional Company",
        "CardCode": "C001", "StreetNoS": "123", "StreetS": "Sample Street",
        "CityS": "Ottawa", "StateS": "ON", "ZipCodeS": "K1A0B1",
        "CountryS": "CA",
    }
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame([row]).to_excel(writer, sheet_name="Orders", index=False)
    st = Store(path)
    st.import_orders(parse_export(bio.getvalue(), "fictional.xlsx"), "fictional.xlsx")
    st.save_draft("42", {
        "weight_lb": 1, "length_in": 6, "width_in": 6, "height_in": 6,
        "pieces": 1, "payer": "Sender",
    })
    st.confirm_ready("42")
    return st


def test_begin_attempt_is_durable_and_unique(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, aid = j.claim("42", environment="development", request_digest="12345678")
    assert j.get(sid)["attempts"][0]["correlation_id"] == aid
    assert Store(p).get_order("42")["status"] == "creating"
    with pytest.raises(ShipmentStateError):
        ShipmentJournal(p).claim("42", environment="development",
                                 request_digest="12345678")


def test_unknown_network_outcome_never_resends(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, _ = j.claim("42", environment="development", request_digest="12345678")
    j.mark_unknown(sid, "network_timeout")
    assert j.get(sid)["status"] == "needs_reconciliation"
    assert j.get(sid)["attempts"][0]["state"] == "unknown"
    with pytest.raises(ShipmentStateError):
        ShipmentJournal(p).claim("42", environment="development",
                                 request_digest="12345678")


def test_pins_and_pdf_survive_restart(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, _ = j.claim("42", environment="development", request_digest="12345678")
    j.mark_created(sid, "PIN-SHIP", ["PIN-PIECE-1", "PIN-PIECE-2"])
    assert j.get(sid)["pieces"][1]["piece_pin"] == "PIN-PIECE-2"
    j.mark_label_pending(sid)
    pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
    output = j.archive_pdf(sid, pdf, tmp_path / "labels")
    assert output.read_bytes() == pdf
    j2 = ShipmentJournal(p)
    assert j2.get(sid)["status"] == "label_ready"
    assert j2.get(sid)["documents"][0]["filename"] == str(output)
    assert Store(p).get_order("42")["status"] == "label_ready"


def test_invalid_pdf_does_not_mark_label_ready(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, _ = j.claim("42", environment="development", request_digest="12345678")
    j.mark_created(sid, "PIN-SHIP", ["PIN-PIECE-1"])
    with pytest.raises(ShipmentStateError):
        j.archive_pdf(sid, b"<html>not PDF</html>", tmp_path / "labels")
    assert j.get(sid)["status"] == "created"


def test_rejected_result_stays_locked(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, _ = j.claim("42", environment="development", request_digest="12345678")
    j.mark_rejected(sid, "VALIDATION_ERROR")
    assert j.get(sid)["status"] == "failed"
    with pytest.raises(ShipmentStateError):
        j.claim("42", environment="development", request_digest="12345678")


def test_concurrent_claims_only_one_wins(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    def claim(_):
        try:
            return bool(ShipmentJournal(p).claim(
                "42", environment="development", request_digest="12345678"
            ))
        except ShipmentStateError:
            return False
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(claim, range(4)))
    assert sum(results) == 1


def test_upgrade_is_repeatable(tmp_path):
    p = tmp_path / "puro.db"
    Store(p)
    ShipmentJournal(p)
    ShipmentJournal(p)
    with sqlite3.connect(p) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 2
        assert db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0] == 0


def test_mark_created_requires_pin_without_partial_commit(tmp_path):
    p = tmp_path / "puro.db"
    ready_order(p)
    j = ShipmentJournal(p)
    sid, _ = j.claim("42", environment="development", request_digest="12345678")
    with pytest.raises(ShipmentStateError):
        j.mark_created(sid, "", ["piece"])
    assert j.get(sid)["status"] == "creating"
