"""Canonical, shipping-only SAP recipient contract.

ALL fields here are sourced from validated order-specific RDR12 *S columns
or approved manual overrides. Never inspect any *B / Bill To fields.
"""
from __future__ import annotations

import re
from typing import Any

CANADIAN_PROVINCES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU",
    "ON", "PE", "QC", "SK", "YT",
}
CANADIAN_POSTAL = re.compile(
    r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d$"
)


class AddressProblem(ValueError):
    """Recipient destination is missing or ambiguous."""


def _text(order: dict[str, Any], key: str) -> str:
    value = order.get(key, "")
    return str(value).strip() if value is not None else ""


def shipping_errors(order: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, label in (
        ("street", "Ship To street"), ("city", "Ship To city"),
        ("province", "Ship To province"), ("postal_code", "Ship To postal code"),
        ("country", "Ship To country"), ("ship_to_name", "Ship To recipient name"),
    ):
        if not _text(order, key):
            errors.append(f"Missing {label}")

    country = _text(order, "country").upper()
    if country and country not in {"CA", "CAN", "CANADA"}:
        errors.append("MVP supports Canadian destinations only")
    province = _text(order, "province").upper()
    if country in {"CA", "CAN", "CANADA"}:
        if province and province not in CANADIAN_PROVINCES:
            errors.append("Ship To province must be a Canadian two-letter code")
        postal = re.sub(r"\s", "", _text(order, "postal_code").upper())
        if postal and not CANADIAN_POSTAL.fullmatch(postal):
            errors.append("Invalid Canadian Ship To postal code")

    # Prevent plausible-looking but unshippable display-only placeholders.
    street = _text(order, "street")
    if street.upper() in {"TBD", "UNKNOWN", "N/A", "SAME AS BILL TO"}:
        errors.append("Ship To street is a placeholder, not a verified destination")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", street):
        errors.append("Ship To street contains invalid control characters")
    return errors


def recipient_snapshot(order: dict[str, Any]) -> dict[str, str]:
    """Create final recipient object. No billing/generic address keys are read.

    Does not split combined street text or invent a house number.
    This dict is the only supported recipient input to the SOAP mapper.
    """
    errors = shipping_errors(order)
    if order.get("line_conflict"):
        errors.append("Line-level Ship To conflict must be resolved first")
    if errors:
        raise AddressProblem("; ".join(errors))
    raw_post = re.sub(r"\s", "", _text(order, "postal_code").upper())
    postal = f"{raw_post[:3]} {raw_post[3:]}"
    return {
        "name": _text(order, "ship_to_name"),
        "street_number": _text(order, "street_no"),
        "street": _text(order, "street"),
        "building": _text(order, "building"),
        "address2": _text(order, "address2"),
        "address3": _text(order, "address3"),
        "city": _text(order, "city"),
        "province": _text(order, "province").upper(),
        "postal_code": postal,
        "country": "CA",
        "phone": _text(order, "phone"),
    }
