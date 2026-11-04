#!/usr/bin/env python3
"""Stage B pre-flight: check Puro's request/response assumptions against REAL local WSDLs.

OFFLINE and read-only. It loads the private Development WSDLs with zeep, builds the
exact SOAP envelopes Puro would send (fictional data, zero credentials) WITHOUT
sending them, and walks the response types for the field paths Puro reads.
Nothing is transmitted; it needs no .env and no API key.

    python scripts/verify_wsdl_wrappers.py --wsdl-dir ~/puro-private/wsdl [--dump DIR]

Output is PASS/FAIL/INFO lines with element names only. --dump writes the
serialized fictional envelopes (no credentials) for inspection; keep them private.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lxml import etree  # noqa: E402
from zeep import Client, Settings  # noqa: E402

from puro.carrier import ALLOWED_OPERATIONS, ENV_HOSTS, WSDL_FILES, request_context  # noqa: E402
from puro.config import AppConfig  # noqa: E402
from puro.documents import documents_request  # noqa: E402
from puro.payloads import create_request, validate_request  # noqa: E402

# Response paths Puro's code reads (puro/shipping.py, puro/documents.py).
RESPONSE_PATHS: dict[tuple[str, str], list[list[str]]] = {
    ("shipping", "ValidateShipment"): [
        ["ResponseInformation", "Errors", "Error", "Code"],
    ],
    ("shipping", "CreateShipment"): [
        ["ResponseInformation", "Errors", "Error", "Code"],
        ["ShipmentPIN", "Value"],
        ["PiecePINs", "PIN", "Value"],
    ],
    ("documents", "GetDocuments"): [
        ["Documents", "Document", "DocumentDetails", "DocumentDetail", "DocumentType"],
        ["Documents", "Document", "DocumentDetails", "DocumentDetail", "DocumentStatus"],
        ["Documents", "Document", "DocumentDetails", "DocumentDetail", "Data"],
    ],
}


# Operations the MVP actually calls; the rest of ALLOWED_OPERATIONS are reserved.
USED_OPERATIONS = frozenset({"ValidateShipment", "CreateShipment", "GetDocuments"})


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def line(self, status: str, label: str, detail: str = "") -> None:
        if status == "FAIL":
            self.failures += 1
        print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def _config() -> AppConfig:
    # Fictional values only. api_key/password stay empty: nothing is ever sent.
    return AppConfig(
        sender_name="Example Sender", sender_street_no="1", sender_street="Test Road",
        sender_city="Ottawa", sender_province="ON", sender_postal_code="K1A0B1",
        sender_phone="613-555-0100", account_number="0000000000",
    )


def _order() -> dict[str, Any]:
    return {
        "doc_num": "600001", "ship_to_name": "Example Receiver", "street_no": "12",
        "street": "Warehouse Lane", "building": "", "address2": "", "address3": "",
        "city": "Ottawa", "province": "ON", "postal_code": "K1A0B1", "country": "CA",
        "phone": "613-555-0123", "weight_lb": 2, "length_in": 6, "width_in": 6,
        "height_in": 6, "service": "PurolatorGround", "payer": "Sender",
    }


def child_type(type_: Any, name: str) -> Any:
    for element_name, element in getattr(type_, "elements", []):
        if element_name == name:
            return element.type
    return None


def has_path(type_: Any, path: list[str]) -> bool:
    for name in path:
        type_ = child_type(type_, name)
        if type_ is None:
            return False
    return True


def top_fields(type_: Any) -> list[str]:
    return [name for name, _ in getattr(type_, "elements", [])]


def _output_type(binding: Any, operation: str) -> Any:
    out = binding.get(operation).output
    body = getattr(out, "body", None)
    return getattr(body, "type", None)


def _request_payload(service: str, operation: str) -> dict | None:
    cfg, order = _config(), _order()
    if (service, operation) == ("shipping", "ValidateShipment"):
        return validate_request(order, cfg, pickup_type="DropOff")
    if (service, operation) == ("shipping", "CreateShipment"):
        return create_request(order, cfg, pickup_type="DropOff")
    if (service, operation) == ("documents", "GetDocuments"):
        return documents_request("000000000000")
    return None


def verify(wsdl_dir: Path, services: tuple[str, ...], dump: Path | None,
           report: Report, environment: str = "development") -> None:
    folder = "Development" if environment == "development" else "Production"
    if environment != "development":
        raise SystemExit("This verifier only supports Development.")
    for service in services:
        filename = WSDL_FILES[service][0]
        path = wsdl_dir / folder / filename
        if not path.is_file():
            report.line("FAIL", f"{service}: WSDL present", f"missing {folder}/{filename}")
            continue
        try:
            client = Client(str(path), settings=Settings(
                strict=True, forbid_dtd=True, forbid_entities=True,
                forbid_external=True, force_https=True))
        except Exception as exc:  # noqa: BLE001 - report, never crash
            report.line("FAIL", f"{service}: WSDL loads", type(exc).__name__ + ": " + str(exc)[:200])
            continue
        report.line("PASS", f"{service}: WSDL loads")

        address = client.service._binding_options["address"]
        url = urlparse(address)
        ok_host = url.scheme == "https" and url.hostname == ENV_HOSTS[environment]
        report.line("PASS" if ok_host else "FAIL", f"{service}: endpoint is https://{ENV_HOSTS[environment]}",
                    "" if ok_host else f"found {url.scheme}://{url.hostname}")

        binding = client.service._binding
        known = set(binding._operations)
        for op in sorted(ALLOWED_OPERATIONS[service]):
            if op in known:
                report.line("PASS", f"{service}: operation {op} exists")
            elif op in USED_OPERATIONS:
                report.line("FAIL", f"{service}: operation {op} exists", f"WSDL operations: {sorted(known)}")
            else:
                report.line("INFO", f"{service}: reserved operation {op} not in WSDL (not used by MVP)")

        try:
            header = request_context(client, service)
            report.line("PASS", f"{service}: RequestContext header builds",
                        "")
        except Exception as exc:  # noqa: BLE001
            report.line("FAIL", f"{service}: RequestContext header builds",
                        type(exc).__name__ + ": " + str(exc)[:200])
            continue

        for op in sorted(ALLOWED_OPERATIONS[service]):
            payload = _request_payload(service, op)
            if payload is None or op not in known:
                continue
            try:
                envelope = client.create_message(client.service, op, _soapheaders=[header], **payload)
                data = etree.tostring(envelope, pretty_print=True)
                report.line("PASS", f"{service}.{op}: request serializes against WSDL ({len(data)} bytes, not sent)")
                if dump:
                    dump.mkdir(parents=True, exist_ok=True)
                    (dump / f"{service}.{op}.request.xml").write_bytes(data)
            except Exception as exc:  # noqa: BLE001
                report.line("FAIL", f"{service}.{op}: request serializes against WSDL",
                            type(exc).__name__ + ": " + str(exc)[:300])

            out_type = _output_type(binding, op)
            if out_type is None:
                report.line("FAIL", f"{service}.{op}: response type found")
                continue
            report.line("INFO", f"{service}.{op}: top-level response fields", ", ".join(top_fields(out_type)))
            for response_path in RESPONSE_PATHS.get((service, op), []):
                dotted = ".".join(response_path)
                present = has_path(out_type, response_path)
                report.line("PASS" if present else "FAIL", f"{service}.{op}: response has {dotted}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wsdl-dir", required=True, help="folder containing Development/*.wsdl")
    ap.add_argument("--dump", help="write serialized fictional request envelopes here")
    ap.add_argument("--services", nargs="*", default=["shipping", "documents"],
                    choices=sorted(WSDL_FILES))
    args = ap.parse_args(argv)
    report = Report()
    verify(Path(args.wsdl_dir).expanduser(), tuple(args.services),
           Path(args.dump).expanduser() if args.dump else None, report)
    print()
    print("Wrapper verification: " + ("ALL PASSED" if report.failures == 0 else f"{report.failures} FAILED"))
    print("Nothing was sent to Purolator.")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
