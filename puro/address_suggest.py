"""Best-effort suggestions that PRE-FILL the review form from SAP's free-text address.

Safety contract (do not weaken):
* Suggestions are display defaults only. They are never written to the order's real
  address fields, never mark anything reviewed, and never reach the carrier.
* The operator must still Save the draft and attest every field against the original
  ORDR.Address2 text (Store.confirm_address_review) before an order can be Ready.
* Anything the parser is unsure about is reported in ``notes`` so the UI can warn.

Typical SAP text (sometimes with line breaks, sometimes flattened to one line):
    <street number> <street name> [UNIT n]
    <CITY> <PROV> <A1A 1A1>
    CANADA
Cash-sale orders put the recipient name BEFORE the street number.
"""
from __future__ import annotations

import re
from typing import Any

PROVINCES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}
PROVINCE_NAMES = {
    "ONTARIO": "ON", "QUEBEC": "QC", "QUÉBEC": "QC", "BRITISH COLUMBIA": "BC",
    "ALBERTA": "AB", "MANITOBA": "MB", "SASKATCHEWAN": "SK", "NOVA SCOTIA": "NS",
    "NEW BRUNSWICK": "NB", "NEWFOUNDLAND AND LABRADOR": "NL", "NEWFOUNDLAND": "NL",
    "PRINCE EDWARD ISLAND": "PE", "YUKON": "YT", "NORTHWEST TERRITORIES": "NT",
    "NUNAVUT": "NU",
}
POSTAL = re.compile(
    r"(?<![A-Z0-9])([ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z])[\s-]?"
    r"(\d[ABCEGHJ-NPRSTV-Z]\d)(?![A-Z0-9])",
    re.I,
)
CANADA = {"CANADA", "CA", "CAN"}
STREET_TYPES = {
    "ST", "STREET", "AVE", "AV", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "BLVD",
    "BOULEVARD", "CRES", "CRESCENT", "CT", "CRT", "COURT", "LANE", "LN", "WAY",
    "PKWY", "PARKWAY", "HWY", "HIGHWAY", "TERR", "TERRACE", "PL", "PLACE", "CIR",
    "CIRCLE", "TRAIL", "TRL", "SQ", "SQUARE", "GATE", "GROVE", "GDNS", "GARDENS",
    "LINE", "PATH", "HILL", "HTS", "HEIGHTS", "SIDEROAD", "CONC", "CONCESSION",
    "MEWS", "ROW", "RUN", "POINT", "PARK", "WALK", "COMMON",
}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST"}
UNIT_WORDS = {"UNIT", "STE", "SUITE", "APT", "APARTMENT"}
NUMBER_START = re.compile(r"^(?:\d{1,6}\s*-\s*)?\d{1,6}[A-Z]?\s+\S", re.I)
NAME_BEFORE_NUMBER = re.compile(
    r"(?<![\w-])(?:\d{1,6}\s*-\s*)?\d{1,6}[A-Z]?\s+[A-Za-z]"
)
PO_BOX = re.compile(r"\bP\.?\s?O\.?\s*BOX\b|\bBOX\s+\d", re.I)
UNIT_AT_END = re.compile(
    r"(?:^|\s)(?:UNIT|STE|SUITE|APT|APARTMENT|#)\s*#?\s*([A-Z0-9][A-Z0-9-]*)\s*$", re.I
)


def _token(word: str) -> str:
    return word.strip(" ,.;").upper()


def _empty() -> dict[str, Any]:
    return {
        "ship_to_name": "", "street_no": "", "street": "", "building": "",
        "address2": "", "city": "", "province": "", "postal_code": "",
        "country": "", "notes": [], "layout": "",
    }


def _split_province(text: str) -> tuple[str, str]:
    """Return (province, remainder) from the end of 'CITY PROV'."""
    words = text.rstrip(" ,.;").split()
    for count in (4, 3, 2, 1):
        if len(words) < count:
            continue
        candidate = _token(" ".join(words[-count:]))
        if candidate in PROVINCE_NAMES:
            return PROVINCE_NAMES[candidate], " ".join(words[:-count]).strip(" ,.;")
        if count == 1 and candidate in PROVINCES:
            return candidate, " ".join(words[:-1]).strip(" ,.;")
    return "", text.strip(" ,.;")


def _split_street_city(combo: str) -> tuple[str, str]:
    """Split 'STREET [DIR] [UNIT n] CITY' using the last street-type word.

    Returns (street_text, city). Empty city means the split could not be made.
    """
    tokens = combo.split()
    type_idx = [i for i, t in enumerate(tokens) if _token(t) in STREET_TYPES and i > 0]
    if not type_idx:
        return combo, ""
    i = type_idx[-1]
    # "KING ST ST CATHARINES": a trailing ST/STE that follows another type word is the
    # start of the city name, so split at the earlier type word instead.
    if _token(tokens[i]) in {"ST", "STE"} and len(type_idx) > 1:
        i = type_idx[-2]
    k = i + 1
    if k < len(tokens) and _token(tokens[k]) in DIRECTIONS:
        k += 1
    if k + 1 < len(tokens) and _token(tokens[k]) in UNIT_WORDS:
        k += 2
    elif k < len(tokens) and tokens[k].startswith("#"):
        k += 1
    return " ".join(tokens[:k]), " ".join(tokens[k:]).strip(" ,.;")


def _parse_street(text: str, out: dict[str, Any]) -> None:
    text = text.strip(" ,;")
    if not text:
        return
    if PO_BOX.search(text):
        out["street"] = text
        out["notes"].append("PO box: confirm Purolator can deliver to it.")
        return
    unit = ""
    m = re.match(r"^(?:(\d+[A-Z]?)\s*-\s*)?(\d+[A-Z]?)\s+(.+)$", text, re.I)
    if m:
        unit, out["street_no"], rest = m.group(1) or "", m.group(2), m.group(3)
    else:
        rest = text
        out["notes"].append("No street number found: check the street line.")
    um = UNIT_AT_END.search(rest)
    if um:
        unit = unit or um.group(1)
        rest = rest[: um.start()].strip(" ,")
    out["street"] = rest.strip(" ,")
    out["building"] = unit


def suggest_address(full_address: str, *, cash_sale: bool = False) -> dict[str, Any]:
    """Return pre-fill suggestions plus ``notes`` describing anything uncertain."""
    out = _empty()
    lines = [
        re.sub(r"\s+", " ", ln).strip(" ,;")
        for ln in re.split(r"[\r\n]+", full_address or "")
    ]
    lines = [ln for ln in lines if ln]
    if not lines:
        out["notes"].append("The SAP address is empty.")
        return out

    postal_idx = next(
        (i for i in range(len(lines) - 1, -1, -1) if POSTAL.search(lines[i])), None
    )
    if postal_idx is None:
        out["notes"].append(
            "No Canadian postal code found in the SAP text: enter every field by hand."
        )
        return out

    line = lines[postal_idx]
    matches = list(POSTAL.finditer(line))
    pm = matches[-1]
    out["postal_code"] = f"{pm.group(1).upper()} {pm.group(2).upper()}"
    after = " ".join([line[pm.end():].strip(" ,;")] + lines[postal_idx + 1:]).strip()
    if not after or _token(after) in CANADA:
        out["country"] = "CA"
    else:
        out["notes"].append(
            f"Country text '{after}' is not Canada. Only Canadian shipments are supported."
        )

    pre = line[: pm.start()].strip(" ,;")
    city_idx = postal_idx
    if not pre and postal_idx > 0:  # postal code alone on its own line
        city_idx = postal_idx - 1
        pre = lines[city_idx]
    out["province"], remainder = _split_province(pre)
    if not out["province"]:
        out["notes"].append("Province not found: check the province.")

    before = lines[:city_idx]
    combined = bool(NUMBER_START.match(remainder)) or (not before and not out["province"])
    if combined or (not before and remainder):
        out["layout"] = "single-line"
        combo = remainder
        name_text = ""
        if cash_sale and not before:
            nm = NAME_BEFORE_NUMBER.search(combo)
            if nm:
                name_text, combo = combo[: nm.start()].strip(" ,;-"), combo[nm.start():]
        street_text, city = _split_street_city(combo)
        out["city"] = city
        if not city:
            out["notes"].append(
                "Could not separate the street from the city: enter both by hand."
            )
        else:
            out["notes"].append(
                "The SAP text has no line breaks: check where the street ends and the city begins."
            )
        _parse_street(street_text, out)
        if cash_sale and before:
            name_text = " ".join(before)
        elif before:
            out["address2"] = ", ".join(before)
        if cash_sale:
            out["ship_to_name"] = name_text
    else:
        out["layout"] = "multi-line"
        out["city"] = remainder
        street_lines = list(before)
        if cash_sale:
            first = next((i for i, ln in enumerate(street_lines) if NUMBER_START.match(ln)), None)
            if first:
                out["ship_to_name"] = " ".join(street_lines[:first])
                street_lines = street_lines[first:]
        main = next(
            (ln for ln in street_lines if NUMBER_START.match(ln) or PO_BOX.search(ln)),
            street_lines[0] if street_lines else "",
        )
        others = [ln for ln in street_lines if ln != main]
        if others:
            out["address2"] = ", ".join(others)
        if not main:
            out["notes"].append("No street line found: check the address.")
        _parse_street(main, out)
        if not out["city"]:
            out["notes"].append("City not found: check the city.")

    if cash_sale and not out["ship_to_name"]:
        out["notes"].append("Cash sale: no recipient name found before the street.")
    return out
