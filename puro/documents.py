"""Purolator Get Documents v1.4 PDF retrieval and strict archive workflow.

The SOAP API can create a shipment without producing immediately printable
documents. This layer never marks label_ready until an actual PDF is validated
and written to local durable storage.
"""
from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from .carrier import CarrierRequestError, PurolatorSOAP
from .journal import ShipmentJournal, ShipmentStateError
from .config import AppConfig


class DocumentProblem(ValueError):
    pass


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _as_list(obj: Any) -> list:
    if obj is None:
        return []
    return obj if isinstance(obj, list) else list(obj) if isinstance(obj, tuple) else [obj]


def _decode_pdf(content: Any) -> bytes:
    if isinstance(content, bytes):
        data = content
    elif isinstance(content, str):
        try:
            data = b64decode(content.strip(), validate=True)
        except (ValueError, Exception):
            raise DocumentProblem("Carrier document contained invalid encoded data") from None
    else:
        raise DocumentProblem("Carrier document data is unavailable")
    if not data.startswith(b"%PDF-"):
        raise DocumentProblem("Carrier returned a non-PDF document")
    return data


def _merge_verified(pdfs: list[bytes], *, expected_pages: int) -> bytes:
    writer = PdfWriter()
    for data in pdfs:
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if not reader.pages:
                raise DocumentProblem("Carrier PDF contains no pages")
            writer.append(reader)
        except DocumentProblem:
            raise
        except Exception:
            raise DocumentProblem("Carrier document is not a readable PDF") from None
    if len(writer.pages) < expected_pages:
        raise DocumentProblem(
            "Label PDF has fewer pages than shipment pieces; verify before printing"
        )
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@dataclass(frozen=True)
class LabelResult:
    shipment_id: str
    status: str
    path: Path | None = None
    message: str = ""


class LabelService:
    def __init__(self, config: AppConfig, journal: ShipmentJournal,
                 carrier: PurolatorSOAP):
        self.config = config
        self.journal = journal
        self.carrier = carrier

    def get_and_archive(self, shipment_id: str,
                        *, production_approved: bool = False) -> LabelResult:
        info = self.journal.get(shipment_id)
        state = info["status"]
        if state == "label_ready":
            docs = info["documents"]
            if docs and Path(docs[0]["filename"]).is_file():
                return LabelResult(shipment_id, "label_ready",
                                   Path(docs[0]["filename"]), "Previously saved PDF")
            raise DocumentProblem("Saved label is missing; investigate before printing")
        if state not in {"created", "label_pending"}:
            raise ShipmentStateError("Shipment has no confirmed PIN for documents")
        pin = info["shipment_pin"]
        if not pin:
            raise DocumentProblem("Shipment PIN missing")
        if state == "created":
            self.journal.mark_label_pending(shipment_id)

        req = {
            "OutputType": "PDF",
            "Synchronous": True,
            "DocumentCriterium": {
                "DocumentCriteria": [{
                    "PIN": {"Value": pin},
                    "DocumentTypes": {"DocumentType": ["DomesticBillOfLading"]},
                }]
            },
        }
        try:
            response = self.carrier.call(
                "documents", "GetDocuments", req,
                reference=f"DOC-{shipment_id[:16]}",
                production_approved=production_approved,
            )
        except CarrierRequestError:
            return LabelResult(shipment_id, "label_pending",
                               message="Label retrieval failed; shipment itself was created")

        if response is None:
            return LabelResult(shipment_id, "label_pending", message="No label document response")
        documents = _as_list(_get(_get(response, "Documents"), "Document"))
        pdfs: list[bytes] = []
        has_pending = False
        errors = []
        for doc in documents:
            details = _as_list(_get(_get(doc, "DocumentDetails"), "DocumentDetail"))
            for detail in details:
                doc_type = str(_get(detail, "DocumentType") or "")
                if doc_type not in {"DomesticBillOfLading", "DomesticBillOfLadingThermal"}:
                    continue
                status = str(_get(detail, "DocumentStatus") or "")
                if status == "Completed":
                    raw = _get(detail, "Data")
                    if raw:
                        pdfs.append(_decode_pdf(raw))
                    else:
                        # Could contain an asynchronous carrier URL. Do not
                        # download arbitrary URLs or declare a label ready.
                        has_pending = True
                elif status in {"Queued", "Pending", "Processing"}:
                    has_pending = True
                elif status == "Error":
                    errors.append("Carrier document generation returned Error")
                else:
                    has_pending = True
        if errors:
            return LabelResult(shipment_id, "label_pending", message="; ".join(errors))
        if has_pending or not pdfs:
            return LabelResult(shipment_id, "label_pending",
                               message="Carrier label is not yet complete; retrieve again later")
        data = _merge_verified(pdfs, expected_pages=len(info["pieces"]))
        filepath = self.journal.archive_pdf(
            shipment_id, data, self.config.labels_dir,
        )
        return LabelResult(shipment_id, "label_ready", filepath, "Printable PDF saved")
