#!/usr/bin/env python3
"""Stage B runner: ONE supervised Purolator DEVELOPMENT shipment (docs/MANUAL_VALIDATION.md).

Hard guards, all enforced before any network call:
  * PUROLATOR_ENV must be development and PURO_PRODUCTION_APPROVED must be false;
  * PURO_ENABLE_CARRIER must be true for THIS process (pass it on the command line; .env stays false);
  * --ship additionally requires --approve-development-shipment;
  * uses a throwaway database/labels folder (never ~/puro-data) and one fixed order, so a second
    CreateShipment is refused by the journal;
  * CreateShipment is NEVER retried: an unclear result ends as needs_reconciliation (exit 4).

Recipient details come from your command line (use Purolator's Developer Test Aid addresses).
Exit codes: 0 ok | 1 carrier/validation said no | 2 refused by a guard | 3 already attempted |
4 needs_reconciliation (do NOT retry; ask) | 5 label problem after a created shipment.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader  # noqa: E402

from puro.carrier import CarrierConfigurationError, CarrierRequestError, PurolatorSOAP  # noqa: E402
from puro.config import AppConfig  # noqa: E402
from puro.documents import LabelService  # noqa: E402
from puro.journal import ShipmentJournal, ShipmentStateError  # noqa: E402
from puro.orders import ParsedImport  # noqa: E402
from puro.payloads import ShipmentValidationError, validate_request  # noqa: E402
from puro.shipping import ShippingCoordinator  # noqa: E402

DOC_ENTRY = "STAGEB-1"


def _refuse(message: str) -> int:
    print(f"REFUSED: {message}")
    return 2


def _order(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "doc_entry": DOC_ENTRY, "doc_num": "STAGEB1", "card_code": "TEST",
        "customer_name": args.name, "ship_to_name": args.name,
        "street_no": args.street_no, "street": args.street, "building": args.unit,
        "address2": "", "address3": "", "city": args.city, "province": args.province,
        "postal_code": args.postal, "country": "CA", "phone": args.phone,
        "service": args.service, "payer": "Sender", "receiver_account": "", "pieces": 1,
        "weight_lb": args.weight_lb, "length_in": args.length_in,
        "width_in": args.width_in, "height_in": args.height_in, "line_conflict": False,
    }


def _errors(response: Any) -> list[str]:
    get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    errs = get(get(get(response, "ResponseInformation"), "Errors"), "Error")
    rows = errs if isinstance(errs, (list, tuple)) else ([errs] if errs else [])
    return [f"{get(e, 'Code')}: {get(e, 'Description')}" for e in rows]


def main(argv: list[str] | None = None,
         carrier_factory: Callable[[AppConfig], Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="ValidateShipment only; creates nothing")
    mode.add_argument("--ship", action="store_true", help="validate, then create exactly one Development shipment")
    ap.add_argument("--approve-development-shipment", action="store_true",
                    help="required with --ship: you approve one Development CreateShipment")
    for flag in ("name", "street-no", "street", "city", "province", "postal", "phone"):
        ap.add_argument(f"--{flag}", required=True)
    ap.add_argument("--unit", default="")
    ap.add_argument("--weight-lb", type=float, default=2.0)
    ap.add_argument("--length-in", type=float, default=8.0)
    ap.add_argument("--width-in", type=float, default=8.0)
    ap.add_argument("--height-in", type=float, default=8.0)
    ap.add_argument("--service", default="PurolatorGround", choices=["PurolatorGround", "PurolatorExpress"])
    ap.add_argument("--pickup", default="DropOff", choices=["DropOff", "PreScheduled"])
    ap.add_argument("--data-dir", default="~/puro-stageB-data")
    ap.add_argument("--labels-dir", default="~/puro-stageB-labels")
    args = ap.parse_args(argv)

    if args.ship and not args.approve_development_shipment:
        return _refuse("--ship needs --approve-development-shipment")
    config = AppConfig.from_environment()
    if config.environment != "development":
        return _refuse("PUROLATOR_ENV is not development")
    if config.production_authorized:
        return _refuse("PURO_PRODUCTION_APPROVED must be false for Stage B")
    if not config.carrier_enabled:
        return _refuse("carrier is disabled; enable it for this one command: "
                       "PURO_ENABLE_CARRIER=true python scripts/stage_b_dev_shipment.py ...")
    missing = config.missing_shipping_settings()
    if missing:
        return _refuse("incomplete settings: " + ", ".join(missing))

    data_dir = Path(args.data_dir).expanduser()
    labels_dir = Path(args.labels_dir).expanduser()
    for d in (data_dir, labels_dir):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    config = dataclasses.replace(config, data_dir=data_dir, labels_dir=labels_dir)
    order = _order(args)
    print(f"Environment: development | mode: {'SHIP (one shipment)' if args.ship else 'validate only'}")
    print(f"Recipient: {args.name}, {args.street_no} {args.street}, {args.city} {args.province} {args.postal}")
    print(f"Service: {args.service} | pickup: {args.pickup} | "
          f"parcel: {args.weight_lb} lb {args.length_in}x{args.width_in}x{args.height_in} in")

    try:
        carrier = (carrier_factory or (lambda c: PurolatorSOAP(c, c.wsdl_dir)))(config)
    except CarrierConfigurationError as exc:
        return _refuse(str(exc))

    if args.validate_only:
        try:
            payload = validate_request(order, config, pickup_type=args.pickup)
            response = carrier.call("shipping", "ValidateShipment", payload, reference="STAGEB-VALIDATE")
        except (ShipmentValidationError, CarrierRequestError, CarrierConfigurationError) as exc:
            print(f"VALIDATE FAILED: {exc}")
            return 1
        get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
        errors, valid = _errors(response), get(response, "ValidShipment")
        print(f"ValidShipment: {valid} | carrier errors: {errors or 'none'}")
        if valid is True and not errors:
            print("VALIDATE OK. Nothing was created.")
            return 0
        print("VALIDATE NOT OK. Nothing was created.")
        return 1

    journal = ShipmentJournal(data_dir / "puro.sqlite3")
    existing = journal.store.get_order(DOC_ENTRY)
    if existing and existing["status"] not in {"needs_review", "ready"}:
        print(f"ALREADY ATTEMPTED (status {existing['status']}). Refusing a second CreateShipment. "
              "Inspect the journal or ask before doing anything else.")
        return 3
    if not existing:
        journal.store.import_orders(ParsedImport([order], [], sha256(b"stage-b").hexdigest()), "stage-b")
    journal.store.save_draft(DOC_ENTRY, {k: order[k] for k in (
        "ship_to_name", "street_no", "street", "building", "city", "province", "postal_code",
        "country", "phone", "service", "payer", "weight_lb", "length_in", "width_in", "height_in")})
    journal.store.confirm_ready(DOC_ENTRY)

    try:
        result = ShippingCoordinator(config, journal, carrier).ship(DOC_ENTRY, pickup_type=args.pickup)
    except ShipmentValidationError as exc:
        print(f"STOPPED BEFORE CREATE (nothing created): {exc}")
        return 1
    except ShipmentStateError as exc:
        print(f"ALREADY ATTEMPTED: {exc}")
        return 3
    print(f"Result: {result.state} | shipment PIN: {result.shipment_pin} | {result.message}")
    if result.state != "created":
        print("NEEDS RECONCILIATION / FAILED. DO NOT RETRY. Report this output and ask.")
        return 4 if result.state == "needs_reconciliation" else 1

    label = LabelService(config, journal, carrier).get_and_archive(result.shipment_id)
    print(f"Label: {label.status} | {label.message}")
    if label.status != "label_ready" or not label.path:
        print("Shipment exists but the label is not ready. Do NOT create another shipment; ask.")
        return 5
    data = Path(label.path).read_bytes()
    reader = PdfReader(BytesIO(data))
    text = " ".join((p.extract_text() or "") for p in reader.pages)
    postal = args.postal.replace(" ", "").upper()
    print(f"PDF: {len(reader.pages)} page(s), {len(data)} bytes, sha256 {sha256(data).hexdigest()[:16]}")
    print(f"PDF text mentions recipient postal code: {postal in text.replace(' ', '').upper()} "
          "(barcodes are images; check them on paper)")
    reopened = ShipmentJournal(data_dir / "puro.sqlite3").get(result.shipment_id)
    print(f"Restart check: status {reopened['status']}, pieces "
          f"{[p['piece_pin'] for p in reopened['pieces']]}, saved label {Path(label.path).name}")
    print("STAGE B SHIPMENT OK. Inspect the PDF, then stop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
