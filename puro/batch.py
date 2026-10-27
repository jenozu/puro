"""Safe sequential batch processing and local history/download exports.

This module has NO scheduler, background requests or automatic retries.
Only an explicit UI/operator action may invoke BatchRunner.run().
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

from .documents import LabelService, DocumentProblem
from .journal import ShipmentJournal, ShipmentStateError
from .payloads import ShipmentValidationError
from .shipping import ShippingCoordinator


@dataclass(frozen=True)
class BatchItem:
    doc_entry: str
    state: str
    shipment_id: str | None = None
    pin: str | None = None
    message: str = ""


class BatchRunner:
    def __init__(self, shipping: ShippingCoordinator, labels: LabelService):
        self.shipping = shipping
        self.labels = labels

    def run(self, doc_entries: list[str], *, pickup_type: str,
            production_approved: bool = False) -> list[BatchItem]:
        """No repeated order identity within a batch. Partial results persist."""
        if not doc_entries:
            raise ValueError("Choose at least one order to ship")
        if len(doc_entries) > 100:
            raise ValueError("Maximum 100 orders per reviewed batch")
        if len(set(doc_entries)) != len(doc_entries):
            raise ValueError("Repeated DocEntry in batch is not permitted")

        results = []
        for doc_entry in doc_entries:
            try:
                result = self.shipping.ship(
                    doc_entry, pickup_type=pickup_type,
                    production_approved=production_approved,
                )
                if result.state == "created" and result.shipment_id:
                    try:
                        label = self.labels.get_and_archive(
                            result.shipment_id,
                            production_approved=production_approved,
                        )
                        results.append(BatchItem(
                            doc_entry, label.status, result.shipment_id,
                            result.shipment_pin, label.message,
                        ))
                    except (DocumentProblem, ShipmentStateError) as exc:
                        # Carrier already accepted the shipment. Do NOT recreate it.
                        results.append(BatchItem(
                            doc_entry, "label_pending", result.shipment_id,
                            result.shipment_pin, "Label requires manual retrieval/verification",
                        ))
                else:
                    results.append(BatchItem(
                        doc_entry, result.state, result.shipment_id,
                        result.shipment_pin, result.message,
                    ))
            except (ShipmentValidationError, ShipmentStateError, ValueError):
                # Rejected locally or previously shipped. Do not blindly retry.
                results.append(BatchItem(
                    doc_entry, "needs_review", message="Order blocked by preflight or prior shipment",
                ))
            except Exception:
                # Do not present success when an unexpected condition occurs.
                results.append(BatchItem(
                    doc_entry, "needs_reconciliation",
                    message="Unconfirmed workflow exception; inspect local shipment history",
                ))
        return results


def history(journal: ShipmentJournal) -> list[dict]:
    """Latest local shipment states and document metadata, no customer addresses."""
    with journal.store._connect() as db:
        rows = db.execute(
            """SELECT s.id AS shipment_id,s.doc_entry,o.doc_num,
                      s.environment,s.status,s.shipment_pin,s.created_at,
                      d.filename,d.checksum
               FROM shipments s
               JOIN orders o ON o.doc_entry=s.doc_entry
               LEFT JOIN documents d ON d.shipment_id=s.id
               ORDER BY s.created_at DESC,s.id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def _csv_safe(value: object) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


def shipment_history_csv(journal: ShipmentJournal) -> bytes:
    buf = StringIO()
    writer = csv.writer(buf)
    columns = ["doc_num", "doc_entry", "environment", "status",
               "shipment_pin", "created_at"]
    writer.writerow(columns)
    for r in history(journal):
        writer.writerow([_csv_safe(r.get(k)) for k in columns])
    return buf.getvalue().encode("utf-8-sig")


def label_zip(journal: ShipmentJournal, selected: list[str] | None = None) -> bytes:
    """Only verified label_ready documents are exported; never regenerate."""
    items = history(journal)
    chosen = set(selected) if selected is not None else None
    output = BytesIO()
    used: set[str] = set()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for r in items:
            if r["status"] != "label_ready":
                continue
            if chosen is not None and r["shipment_id"] not in chosen:
                continue
            filename = r.get("filename")
            if not filename:
                continue
            path = Path(filename)
            if not path.is_file():
                continue
            data = path.read_bytes()
            if sha256(data).hexdigest() != r.get("checksum"):
                continue
            safe_order = re.sub(r"[^a-zA-Z0-9_-]", "_", str(r["doc_num"]))[:32]
            safe_pin = re.sub(r"[^a-zA-Z0-9_-]", "_", str(r["shipment_pin"]))[:32]
            name = f"{safe_order}_{safe_pin}.pdf"
            if name in used:
                continue
            used.add(name)
            archive.writestr(name, data)
    return output.getvalue()
