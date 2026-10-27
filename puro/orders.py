"""SAP Business One XLSX/CSV import and safety checks.

Shipping addresses come ONLY from RDR12's *S (Ship To) fields or
explicitly mapped ShipTo* columns. Generic and *B billing columns are
never eligible as a destination. No fuzzy address-field matching.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import re
from zipfile import BadZipFile, ZipFile
from typing import Any

import pandas as pd

from .address import shipping_errors


class ImportProblem(ValueError):
    """Import is unusable; user should correct the workbook/header."""


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).casefold())


ALIASES: dict[str, tuple[str, ...]] = {
    "doc_entry": ("DocEntry", "Internal Number"),
    "doc_num": ("DocNum", "Document Number"),
    "card_code": ("CardCode", "Customer/Vendor Code"),
    "customer_name": ("CustomerName", "CardName"),
    "customer_po": ("CustomerPO", "NumAtCard"),
    "full_ship_to_address": ("FullShipToAddress",),
    "ship_to_code": ("ShipToCode",),
    "ship_to_name": ("ShipToName", "AddressNameS"),
    "street_no": ("ShipToStreetNo", "StreetNoS"),
    "street": ("ShipToStreet", "StreetS"),
    "building": ("ShipToBuilding", "BuildingS"),
    "address2": ("ShipToAddress2", "Address2S"),
    "address3": ("ShipToAddress3", "Address3S"),
    "city": ("ShipToCity", "CityS"),
    "province": ("ShipToProvince", "StateS"),
    "postal_code": ("ShipToPostalCode", "ZipCodeS"),
    "country": ("ShipToCountry", "CountryS"),
    "phone": ("ContactPhone", "CustomerPhone", "Phone1"),
    "doc_status": ("DocStatus",),
    "cancelled": ("CANCELED", "Canceled"),
}
LINE_ALIASES = {"doc_entry": ("DocEntry",), "ship_to_code": ("LineShipToCode", "ShipToCode")}

DEFAULT_PACKAGE = {
    "weight_lb": 0.0,
    "length_in": 0.0,
    "width_in": 0.0,
    "height_in": 0.0,
    "pieces": 1,
    "service": "PurolatorGround",
    "payer": "Sender",
    "receiver_account": "",
}


def _mapped(df: pd.DataFrame, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    labels: dict[str, list[str]] = {}
    for c in df.columns:
        labels.setdefault(_norm(c), []).append(str(c))
    result: dict[str, str] = {}
    for field, options in aliases.items():
        matches = []
        for option in options:
            matches.extend(labels.get(_norm(option), []))
        # Multiple aliases can normalize to the *same* physical column,
        # e.g. CANCELED and Canceled. Count each actual column only once.
        matches = list(dict.fromkeys(matches))
        if len(matches) > 1:
            raise ImportProblem(f"Ambiguous columns for {field}: {', '.join(matches)}")
        if matches:
            result[field] = matches[0]
    return result


def _val(row: pd.Series, mapping: dict[str, str], field: str) -> str:
    if field not in mapping:
        return ""
    value = row[mapping[field]]
    if pd.isna(value):
        return ""
    s = str(value).strip()
    # Excel numeric document identifiers commonly arrive as 123.0.
    if field in {"doc_entry", "doc_num"} and re.fullmatch(r"\d+\.0", s):
        return s[:-2]
    return s


def validate_address(order: dict[str, Any]) -> list[str]:
    """Validate only order-specific Ship To fields, never billing fields."""
    return shipping_errors(order)


def validate_package(order: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("weight_lb", "length_in", "width_in", "height_in"):
        try:
            if float(order.get(field, 0)) <= 0:
                errors.append(f"Enter a positive {field.replace('_', ' ')}")
        except (ValueError, TypeError):
            errors.append(f"Invalid {field}")
    try:
        if int(order.get("pieces", 0)) != 1:
            errors.append("First increment supports one parcel per reviewed order")
    except (ValueError, TypeError):
        errors.append("Invalid parcel count")
    if order.get("payer") == "Receiver" and not str(
        order.get("receiver_account", "")
    ).strip():
        errors.append("Receiver billing requires a Purolator account number")
    return errors


def review_errors(order: dict[str, Any]) -> list[str]:
    errors = validate_address(order) + validate_package(order)
    if order.get("line_conflict"):
        errors.append("Order lines use conflicting/different Ship To codes; split and verify in SAP")
    return errors


@dataclass
class ParsedImport:
    orders: list[dict[str, Any]]
    issues: list[str]
    sha256: str


def parse_export(file_bytes: bytes, filename: str) -> ParsedImport:
    """Parse an untrusted workbook into review-only normalized order dicts.

    Does not execute macros/formulas. Row-level invalid destinations remain
    available for user correction. It makes no requests to Purolator.
    """
    if not file_bytes:
        raise ImportProblem("The uploaded file is empty")
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ImportProblem("File exceeds 20 MB import limit")
    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        if ext == "csv":
            orders_df = pd.read_csv(BytesIO(file_bytes), dtype=str, keep_default_na=False)
            lines_df = None
        elif ext == "xlsx":
            # ZIP metadata check before invoking the Excel reader. Files that
            # decompress into huge XML payloads could exhaust workstation RAM.
            with ZipFile(BytesIO(file_bytes)) as archive:
                members = archive.infolist()
                if len(members) > 1500 or sum(m.file_size for m in members) > 80 * 1024 * 1024:
                    raise ImportProblem("Workbook expands beyond the safe import limit")
                if any(m.file_size > 40 * 1024 * 1024 for m in members):
                    raise ImportProblem("Workbook contains an oversized XML component")
            sheets = pd.read_excel(
                BytesIO(file_bytes), sheet_name=None, dtype=str,
                keep_default_na=False, engine="openpyxl",
            )
            names = {k.casefold(): k for k in sheets}
            if "orders" in names:
                orders_df = sheets[names["orders"]]
            elif len(sheets) == 1:
                orders_df = next(iter(sheets.values()))
            else:
                raise ImportProblem("Workbook must contain an 'Orders' worksheet when multiple sheets exist")
            lines_df = sheets.get(names["orderlines"]) if "orderlines" in names else None
        else:
            raise ImportProblem("Upload an .xlsx or .csv file (not .xls/.xlsm)")
    except ImportProblem:
        raise
    except Exception as exc:
        raise ImportProblem(f"Could not read file: {type(exc).__name__}") from exc

    if orders_df.empty:
        raise ImportProblem("Orders sheet contains no rows")
    if len(orders_df) > 5000:
        raise ImportProblem("Import is limited to 5,000 rows")
    m = _mapped(orders_df, ALIASES)
    needed = {"doc_entry", "doc_num", "card_code", "customer_name"}
    if needed - m.keys():
        raise ImportProblem(f"Missing columns: {', '.join(sorted(needed - m.keys()))}")
    full_address_mode = "full_ship_to_address" in m
    if not full_address_mode and not any(f in m for f in ("street", "city", "postal_code", "country")):
        raise ImportProblem("No approved Ship To columns detected; Bill To data is not accepted")

    issues: list[str] = []
    line_codes: dict[str, set[str]] = {}
    if lines_df is not None and not lines_df.empty:
        if len(lines_df) > 25000:
            raise ImportProblem("OrderLines sheet exceeds 25,000 rows")
        lm = _mapped(lines_df, LINE_ALIASES)
        if "doc_entry" not in lm or "ship_to_code" not in lm:
            raise ImportProblem(
                "OrderLines requires DocEntry and LineShipToCode (or ShipToCode)"
            )
        if "doc_entry" in lm and "ship_to_code" in lm:
            for _, ln in lines_df.iterrows():
                key = _val(ln, lm, "doc_entry")
                code = _val(ln, lm, "ship_to_code")
                if key and code:
                    line_codes.setdefault(key, set()).add(code.casefold())

    orders: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in orders_df.iterrows():
        record = {field: _val(row, m, field) for field in ALIASES}
        entry = record["doc_entry"]
        if not entry:
            issues.append(f"Row {int(index)+2}: no DocEntry; skipped")
            continue
        if not record["doc_num"] or not record["card_code"] or not record["customer_name"]:
            issues.append(
                f"Row {int(index)+2}: missing DocNum, CardCode or CustomerName; skipped"
            )
            continue
        if entry in seen:
            issues.append(f"Row {int(index)+2}: duplicate DocEntry {entry}; skipped")
            continue
        seen.add(entry)
        status = record["doc_status"].upper()
        cancelled = record["cancelled"].upper()
        if (status and status not in {"O", "OPEN"}) or cancelled in {"Y", "YES"}:
            issues.append(f"Row {int(index)+2}: closed/cancelled order; skipped")
            continue
        if full_address_mode:
            # ORDR.Address2 is the sole source for destination data in this format.
            # No parsing guess is used for carrier submission.
            for key in ("ship_to_name", "street_no", "street", "building", "address2",
                        "address3", "city", "province", "postal_code", "country"):
                record[key] = ""
            name = record["customer_name"].strip()
            record["cash_sale"] = bool(re.fullmatch(r"cash\s+sale", name, re.I))
            if not record["cash_sale"]:
                record["ship_to_name"] = name
            else:
                # This is only a suggestion. Operator approval is always required.
                match = re.search(r"\\b\\d{1,6}\\s+[A-Za-z]", record["full_ship_to_address"])
                record["suggested_cash_recipient"] = (
                    record["full_ship_to_address"][:match.start()].strip(" ,;-")
                    if match else ""
                )
            # Even regular customer names do not mean the street/city can be guessed.
            record["address_review_pending"] = True
        else:
            record["ship_to_name"] = record["ship_to_name"] or record["customer_name"]
            record["address_review_pending"] = False
        codes = line_codes.get(entry, set())
        header = record["ship_to_code"].casefold()
        record["line_conflict"] = bool(
            len(codes) > 1 or (header and codes and codes != {header})
        )
        record["source_address"] = (
            {"full_ship_to_address": record["full_ship_to_address"]}
            if full_address_mode else {
                k: record[k] for k in (
                    "ship_to_code", "ship_to_name", "street_no", "street",
                    "building", "address2", "address3", "city", "province",
                    "postal_code", "country"
                )
            }
        )
        record.update(DEFAULT_PACKAGE)
        record["source_row"] = int(index) + 2
        record["import_warnings"] = validate_address(record)
        if full_address_mode and not record["full_ship_to_address"]:
            record["import_warnings"].append("Missing FullShipToAddress from ORDR.Address2")
        orders.append(record)
    unseen_line_orders = sorted(set(line_codes) - seen)
    if unseen_line_orders:
        issues.append(
            f"OrderLines references {len(unseen_line_orders)} unimported DocEntry values; "
            "review workbook filters"
        )
    return ParsedImport(orders=orders, issues=issues, sha256=sha256(file_bytes).hexdigest())
