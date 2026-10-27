"""Validated Purolator Shipping Service v2.2 request data (no network calls).

Field names were checked against Purolator ShippingService v2 WSDL supplied
privately to the project. This module never reads SAP Bill To columns.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any

from .address import AddressProblem, recipient_snapshot
from .config import AppConfig


class ShipmentValidationError(ValueError):
    """Local field failure: no request should be sent to carrier."""


SERVICES = frozenset({"PurolatorGround", "PurolatorExpress"})
PICKUP_TYPES = frozenset({"DropOff", "PreScheduled"})
PAYER_TYPES = frozenset({"Sender", "Receiver"})


def _phone(value: str) -> dict[str, str]:
    # Strict Canadian/NANP phone parsing; do not invent a carrier contact number.
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        raise ShipmentValidationError("A ten-digit contact phone is required")
    return {
        "CountryCode": "1",
        "AreaCode": digits[:3],
        "Phone": digits[3:],
    }


def _decimal(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("not positive")
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ShipmentValidationError(f"Provide a positive {label}") from None


def _sender(config: AppConfig) -> dict[str, Any]:
    required = (
        "sender_name", "sender_street", "sender_city", "sender_province",
        "sender_postal_code", "sender_phone", "account_number"
    )
    if any(not str(getattr(config, k, "")).strip() for k in required):
        raise ShipmentValidationError("Warehouse sender/contact and account settings are incomplete")
    if config.sender_country.upper() != "CA":
        raise ShipmentValidationError("MVP requires a Canadian sender")
    return {
        "Name": config.sender_name,
        "StreetNumber": config.sender_street_no,
        "StreetName": config.sender_street,
        "City": config.sender_city,
        "Province": config.sender_province.upper(),
        "Country": "CA",
        "PostalCode": config.sender_postal_code,
        "PhoneNumber": _phone(config.sender_phone),
    }


def _receiver(order: dict[str, Any]) -> dict[str, Any]:
    try:
        dest = recipient_snapshot(order)
    except AddressProblem as exc:
        raise ShipmentValidationError(str(exc)) from None
    return {
        "Name": dest["name"],
        "StreetNumber": dest["street_number"],
        "StreetName": dest["street"],
        "Suite": dest["building"],
        "StreetAddress2": dest["address2"],
        "StreetAddress3": dest["address3"],
        "City": dest["city"],
        "Province": dest["province"],
        "Country": dest["country"],
        "PostalCode": dest["postal_code"],
        "PhoneNumber": _phone(dest["phone"]),
    }


def _piece(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "Weight": {"Value": _decimal(package.get("weight_lb"), "weight (lb)"),
                   "WeightUnit": "lb"},
        "Length": {"Value": _decimal(package.get("length_in"), "length (in)"),
                   "DimensionUnit": "in"},
        "Width": {"Value": _decimal(package.get("width_in"), "width (in)"),
                  "DimensionUnit": "in"},
        "Height": {"Value": _decimal(package.get("height_in"), "height (in)"),
                   "DimensionUnit": "in"},
    }


def shipment_body(order: dict[str, Any], config: AppConfig,
                  *, pickup_type: str, packages: list[dict[str, Any]] | None = None) -> dict:
    """Generate the WSDL-named Shipment object, not a complete SOAP envelope.

    One package per order is supported by default; explicit 'packages' allows
    multiple independently measured cartons. No inferred item dimensions.
    """
    if pickup_type not in PICKUP_TYPES:
        raise ShipmentValidationError("Choose DropOff or PreScheduled pickup")
    service = str(order.get("service", "")).strip()
    if service not in SERVICES:
        raise ShipmentValidationError("Requested carrier service has not been approved")
    payer = str(order.get("payer", ""))
    if payer not in PAYER_TYPES:
        raise ShipmentValidationError("Unsupported billing type")
    receiver_account = str(order.get("receiver_account", "")).strip()
    if payer == "Receiver" and not receiver_account:
        raise ShipmentValidationError("Receiver Purolator account required for collect shipping")
    parcel_list = packages if packages is not None else [order]
    if not parcel_list or len(parcel_list) > 50:
        raise ShipmentValidationError("Provide 1–50 physically measured parcels")
    if packages is None and int(order.get("pieces", 1)) != 1:
        raise ShipmentValidationError("Explicit per-parcel measurements are required for multiple pieces")
    pieces = [_piece(p) for p in parcel_list]
    total_weight = sum((p["Weight"]["Value"] for p in pieces), Decimal(0))
    shipping = {
        "SenderInformation": {"Address": _sender(config)},
        "ReceiverInformation": {"Address": _receiver(order)},
        "PackageInformation": {
            "ServiceID": service,
            "TotalWeight": {"Value": total_weight, "WeightUnit": "lb"},
            "TotalPieces": len(pieces),
            "PiecesInformation": {"Piece": pieces},
        },
        "PaymentInformation": {
            "PaymentType": payer,
            "RegisteredAccountNumber": config.account_number,
            **({"BillingAccountNumber": receiver_account} if payer == "Receiver" else {}),
        },
        "PickupInformation": {"PickupType": pickup_type},
    }
    if order.get("doc_num"):
        shipping["TrackingReferenceInformation"] = {
            "Reference1": str(order["doc_num"]).strip()[:35]
        }
    return shipping


def create_request(order: dict[str, Any], config: AppConfig,
                   *, pickup_type: str, packages: list[dict] | None = None) -> dict:
    """Matches CreateShipmentRequestContainer (Shipment + PrinterType)."""
    return {
        "Shipment": shipment_body(order, config,
                                  pickup_type=pickup_type, packages=packages),
        "PrinterType": "Regular",
    }


def validate_request(order: dict[str, Any], config: AppConfig,
                     *, pickup_type: str, packages: list[dict] | None = None) -> dict:
    return {"Shipment": shipment_body(order, config,
                                      pickup_type=pickup_type, packages=packages)}


def request_digest(body: dict[str, Any]) -> str:
    """Non-sensitive checksum; do not log full request or recipient address."""
    normalized = json.dumps(body, sort_keys=True, default=str)
    return sha256(normalized.encode("utf-8")).hexdigest()
