#!/usr/bin/env python3
"""Stage A helper (docs/MANUAL_VALIDATION.md): fictional workbooks + offline checks.

Writes two synthetic SAP workbooks and runs the importer/store/payload rules
against them in a throwaway temp database. No network, no carrier, no real data,
no .env, no credentials. Output files are untracked (*.xlsx is gitignored); the
default output folder is outside the repository.

    python scripts/stage_a_check.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from puro.config import AppConfig  # noqa: E402
from puro.orders import parse_export, review_errors  # noqa: E402
from puro.payloads import shipment_body  # noqa: E402
from puro.store import Store  # noqa: E402

LEGACY_NAME = "synthetic_legacy_rdr12.xlsx"
COPY_TABLE_NAME = "synthetic_sap_copy_table.xlsx"

# Bill To values that must never appear in any receiver payload.
BILL_TO_MARKERS = ("Toronto", "88 Accounts", "M5V")


def _legacy_row(entry: int, **over: str) -> dict[str, str]:
    row = {
        "DocEntry": str(entry), "DocNum": str(600000 + entry), "CardCode": "C900",
        "CardName": "Example Repairs Inc.", "DocStatus": "O", "CANCELED": "N",
        "ShipToCode": "WAREHOUSE", "AddressNameS": "Example Repairs Warehouse",
        "StreetNoS": "12", "StreetS": "Warehouse Lane", "CityS": "Ottawa",
        "StateS": "ON", "ZipCodeS": "K1A0B1", "CountryS": "CA",
        # Bill To differs on purpose: Toronto. The importer must ignore all *B columns.
        "StreetNoB": "88", "StreetB": "Accounts Avenue", "CityB": "Toronto",
        "StateB": "ON", "ZipCodeB": "M5V2T6", "CountryB": "CA",
    }
    row.update(over)
    return row


def build_legacy() -> bytes:
    orders = [
        _legacy_row(9001),                                  # good: Ship To Ottawa, Bill To Toronto
        _legacy_row(9002, StreetS="", StreetNoS=""),        # empty street
        _legacy_row(9003, ZipCodeS=""),                     # empty postal code
        _legacy_row(9004, CountryS="US", StateS="NY"),      # country outside MVP
        _legacy_row(9005, CountryS="XX"),                   # invalid country
        _legacy_row(9006),                                  # distinct line Ship To codes
    ]
    lines = [
        {"DocEntry": "9001", "LineNum": "0", "ItemCode": "A1", "LineShipToCode": "WAREHOUSE"},
        {"DocEntry": "9006", "LineNum": "0", "ItemCode": "A1", "LineShipToCode": "WAREHOUSE"},
        {"DocEntry": "9006", "LineNum": "1", "ItemCode": "B2", "LineShipToCode": "DEPOT"},
    ]
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as wr:
        pd.DataFrame(orders).to_excel(wr, sheet_name="Orders", index=False)
        pd.DataFrame(lines).to_excel(wr, sheet_name="OrderLines", index=False)
    return out.getvalue()


def build_copy_table() -> bytes:
    rows = [
        {"Internal Number": 9101, "Document Number": 609101,
         "Customer/Vendor Code": "C901", "CustomerName": "Example Industries",
         "CustomerPO": "PO-TEST-1",
         "FullShipToAddress": "12 Warehouse Lane Unit 4 Ottawa ON K1A 0B1 CANADA"},
        {"Internal Number": 9102, "Document Number": 609102,
         "Customer/Vendor Code": "CASH", "CustomerName": "CASH SALE", "CustomerPO": "",
         "FullShipToAddress": "Acme Sample Repair 140 Fictional St Russell ON K0A 1W0 Canada"},
        {"Internal Number": 9103, "Document Number": 609103,
         "Customer/Vendor Code": "C902", "CustomerName": "Blank Address Co",
         "CustomerPO": "", "FullShipToAddress": ""},
    ]
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as wr:
        pd.DataFrame(rows).to_excel(wr, sheet_name="Sheet1", index=False)
    return out.getvalue()


class Results:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail and not ok else ""))
        if not ok:
            self.failed += 1


def _contains(errors: list[str], text: str) -> bool:
    return any(text.lower() in e.lower() for e in errors)


def _fake_config() -> AppConfig:
    # Entirely fictional sender; no credentials.
    return AppConfig(
        sender_name="Example Sender", sender_street_no="1", sender_street="Test Road",
        sender_city="Ottawa", sender_province="ON", sender_postal_code="K1A0B1",
        sender_phone="613-555-0100", account_number="0000000000",
    )


def run_checks(legacy: bytes, copy_table: bytes) -> int:
    r = Results()
    parsed = parse_export(legacy, LEGACY_NAME)
    by = {o["doc_entry"]: o for o in parsed.orders}
    r.check("legacy: all 6 synthetic orders imported", len(by) == 6, f"got {sorted(by)}")

    # Stage A step 2: Ship To (Ottawa) used, Bill To (Toronto) never ingested.
    good = by["9001"]
    r.check("legacy: Ship To city is Ottawa", good["city"] == "Ottawa")
    r.check("legacy: no Bill To value stored in order record",
            not any(m in json.dumps(good) for m in BILL_TO_MARKERS))
    order = dict(good, phone="613-555-0123", weight_lb=2, length_in=6, width_in=6,
                 height_in=6, service="PurolatorGround", payer="Sender")
    body = shipment_body(order, _fake_config(), pickup_type="DropOff")
    receiver = body["ReceiverInformation"]["Address"]
    r.check("payload: receiver is Ottawa Ship To", receiver["City"] == "Ottawa"
            and receiver["StreetName"] == "Warehouse Lane" and receiver["PostalCode"] == "K1A 0B1")
    r.check("payload: no Bill To value in receiver",
            not any(m in json.dumps(receiver, default=str) for m in BILL_TO_MARKERS))

    # Stage A step 3: blocks.
    r.check("blocks empty Ship To street",
            _contains(review_errors(by["9002"]), "Missing Ship To street"))
    r.check("blocks empty Ship To postal code",
            _contains(review_errors(by["9003"]), "Missing Ship To postal code"))
    r.check("blocks non-Canadian country (US)",
            _contains(review_errors(by["9004"]), "Canadian destinations only"))
    r.check("blocks invalid country (XX)",
            _contains(review_errors(by["9005"]), "Canadian destinations only"))
    r.check("blocks distinct line Ship To codes",
            by["9006"]["line_conflict"] and _contains(review_errors(by["9006"]), "conflicting"))
    r.check("good order has no address errors (package still needed)",
            not [e for e in review_errors(order) if "Ship To" in e])

    # Stage A step 4: edit revokes Ready; reimport and "restart" preserve the stored order.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "stage_a.sqlite3"
        store = Store(db)
        store.import_orders(parsed, LEGACY_NAME)
        pkg = {"weight_lb": 2, "length_in": 6, "width_in": 6, "height_in": 6,
               "phone": "613-555-0123"}
        store.save_draft("9001", pkg)
        store.confirm_ready("9001")
        r.check("order can be marked Ready after review",
                store.get_order("9001")["status"] == "ready")
        store.save_draft("9001", {"street": "Edited Lane"})
        r.check("editing the address revokes Ready",
                store.get_order("9001")["status"] == "needs_review")
        again = store.import_orders(parsed, LEGACY_NAME)
        r.check("reimport inserts nothing new", again == (0, 6), str(again))
        reopened = Store(db)  # fresh object == process restart
        kept = reopened.get_order("9001")
        r.check("restart + reimport preserve the edited address",
                kept["data"]["street"] == "Edited Lane" and kept["status"] == "needs_review")

        # Copy-table (current approved SAP export) mode.
        cp = parse_export(copy_table, COPY_TABLE_NAME)
        cby = {o["doc_entry"]: o for o in cp.orders}
        store.import_orders(cp, COPY_TABLE_NAME)
        r.check("copy-table: 3 orders imported", len(cby) == 3)
        r.check("copy-table: structured address fields left empty (no guessing)",
                cby["9101"]["street"] == "" and cby["9101"]["city"] == ""
                and cby["9101"]["address_review_pending"])
        r.check("copy-table: Cash Sale recipient only suggested, not set",
                cby["9102"]["cash_sale"] and cby["9102"]["ship_to_name"] == ""
                and cby["9102"]["suggested_cash_recipient"] == "Acme Sample Repair")
        r.check("copy-table: blank FullShipToAddress flagged",
                _contains(cby["9103"]["import_warnings"], "Missing FullShipToAddress"))
        try:
            store.confirm_ready("9101")
            blocked = False
        except ValueError:
            blocked = True
        r.check("copy-table: cannot be Ready before address is entered and attested", blocked)

    print()
    print("Stage A offline checks: " + ("ALL PASSED" if r.failed == 0 else f"{r.failed} FAILED"))
    print("Still manual (browser): UI has no enabled Create Shipment control; see runbook.")
    return 1 if r.failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="~/puro-stageA", help="folder for the synthetic workbooks")
    args = ap.parse_args(argv)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    legacy, copy_table = build_legacy(), build_copy_table()
    (out / LEGACY_NAME).write_bytes(legacy)
    (out / COPY_TABLE_NAME).write_bytes(copy_table)
    print(f"Wrote {out / LEGACY_NAME}\nWrote {out / COPY_TABLE_NAME}\n")
    return run_checks(legacy, copy_table)


if __name__ == "__main__":
    raise SystemExit(main())
