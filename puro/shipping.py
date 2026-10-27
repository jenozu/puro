"""Orchestrate a single reviewed shipment with durable claims and no blind retries.

No UI calls this until deliberate Task 10 integration and Task 12 authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .carrier import CarrierOutcomeUnknown, CarrierRequestError, PurolatorSOAP
from .journal import ShipmentJournal, ShipmentStateError
from .payloads import ShipmentValidationError, create_request, request_digest, validate_request
from .config import AppConfig


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _carrier_errors(response: Any) -> list[str]:
    info = _get(response, "ResponseInformation")
    errs = _get(_get(info, "Errors"), "Error")
    if not errs:
        return []
    entries = errs if isinstance(errs, (list, tuple)) else [errs]
    return [str(_get(e, "Code") or "carrier_rejected")[:64] for e in entries]


def _pin_value(value: Any) -> str:
    result = _get(value, "Value")
    return str(result).strip() if result is not None else ""


def _piece_pins(response: Any) -> list[str]:
    entries = _get(_get(response, "PiecePINs"), "PIN")
    if not entries:
        return []
    rows = entries if isinstance(entries, (list, tuple)) else [entries]
    return [v for v in (_pin_value(p) for p in rows) if v]


@dataclass(frozen=True)
class ShipResult:
    shipment_id: str | None
    state: str
    shipment_pin: str | None = None
    message: str = ""


class ShippingCoordinator:
    def __init__(self, config: AppConfig, journal: ShipmentJournal,
                 carrier: PurolatorSOAP):
        self.config = config
        self.journal = journal
        self.carrier = carrier

    def ship(self, doc_entry: str, *, pickup_type: str,
             production_approved: bool = False,
             packages: list[dict] | None = None) -> ShipResult:
        """Review => ValidateShipment => atomically claim => CreateShipment once.

        Operators must never call this concurrently against the same order
        except through the journal, which serializes and rejects duplicate claims.
        """
        order = self.journal.store.get_order(str(doc_entry))
        if not order or order["status"] != "ready":
            raise ShipmentStateError("Only reviewed Ready orders can be submitted")

        # Local validation occurs BEFORE journal claim or any carrier request.
        payload = create_request(order["data"], self.config,
                                 pickup_type=pickup_type, packages=packages)
        validation = validate_request(order["data"], self.config,
                                      pickup_type=pickup_type, packages=packages)
        if self.config.environment == "production" and not production_approved:
            raise ShipmentValidationError("Production requires explicit confirmation")

        # Carrier validation is safe to repeat, but a missing/faulty response is
        # NOT permission to create a shipment.
        result = self.carrier.call(
            "shipping", "ValidateShipment", validation,
            reference=f"PRE-{doc_entry}", production_approved=production_approved,
        )
        if result is None:
            raise ShipmentValidationError("Carrier did not confirm validation")
        errors = _carrier_errors(result)
        if errors:
            raise ShipmentValidationError("Carrier validation rejected: " + ",".join(errors))

        shipment_id, attempt_id = self.journal.claim(
            str(doc_entry), environment=self.config.environment,
            request_digest=request_digest(payload),
        )
        try:
            outcome = self.carrier.call(
                "shipping", "CreateShipment", payload,
                reference=attempt_id, production_approved=production_approved,
            )
        except CarrierOutcomeUnknown:
            self.journal.mark_unknown(shipment_id, "carrier_response_unknown")
            return ShipResult(shipment_id, "needs_reconciliation",
                              message="Carrier outcome uncertain; verify before resubmitting")
        except Exception:
            # Even an unexpected exception after claim might mean the request
            # reached the carrier. Never default to failed + retry.
            self.journal.mark_unknown(shipment_id, "unconfirmed_carrier_exception")
            return ShipResult(shipment_id, "needs_reconciliation",
                              message="Carrier outcome unconfirmed; manual reconciliation needed")

        if outcome is None:
            self.journal.mark_unknown(shipment_id, "empty_carrier_response")
            return ShipResult(shipment_id, "needs_reconciliation",
                              message="Empty carrier response; verify externally")

        shipment_pin = _pin_value(_get(outcome, "ShipmentPIN"))
        errs = _carrier_errors(outcome)
        if errs and not shipment_pin:
            self.journal.mark_rejected(shipment_id, errs[0])
            return ShipResult(shipment_id, "failed",
                              message="Carrier explicitly rejected shipment: " + errs[0])

        piece_pins = _piece_pins(outcome)
        expected = payload["Shipment"]["PackageInformation"]["TotalPieces"]
        # Single-piece shipment may use shipment PIN as the piece identifier.
        if expected == 1 and not piece_pins and shipment_pin:
            piece_pins = [shipment_pin]
        if not shipment_pin or len(piece_pins) != expected or errs:
            self.journal.mark_unknown(
                shipment_id, "incomplete_carrier_pins", shipment_pin=shipment_pin or None,
            )
            return ShipResult(
                shipment_id, "needs_reconciliation", shipment_pin or None,
                "Carrier response needs PIN reconciliation",
            )
        self.journal.mark_created(shipment_id, shipment_pin, piece_pins)
        return ShipResult(shipment_id, "created", shipment_pin,
                          "Shipment created; document retrieval still required")
