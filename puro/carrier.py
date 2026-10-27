"""Purolator E-Ship SOAP adapter — WSDL-driven, secrets never logged.

The adapter does not construct carrier-specific shipment bodies; those are
handled by the validated domain mapper (Task 7). No UI calls this yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
from zeep.exceptions import Error as ZeepError
from zeep.transports import Transport

from .config import AppConfig


WSDL_FILES = {
    "shipping": ("ShippingService.wsdl", "2.2"),
    "documents": ("ShippingDocumentsService.wsdl", "1.4"),
    "estimates": ("EstimatingService.wsdl", "2.2"),
    "availability": ("ServiceAvailabilityService.wsdl", "2.0"),
}
ALLOWED_OPERATIONS = {
    "shipping": frozenset({"ValidateShipment", "CreateShipment", "VoidShipment"}),
    "documents": frozenset({"GetDocuments"}),
    "estimates": frozenset({"GetFullEstimate", "GetQuickEstimate"}),
    "availability": frozenset({"GetServicesOptions", "ValidateCityPostalCodeZip"}),
}
ENV_HOSTS = {
    "development": "devwebservices.purolator.com",
    "production": "webservices.purolator.com",
}


class CarrierConfigurationError(ValueError):
    """Safe local setup failure: never contains a key or password."""


class CarrierRequestError(RuntimeError):
    """Carrier request rejected/failed; sanitized description only."""


class CarrierOutcomeUnknown(CarrierRequestError):
    """CreateShipment may have reached Purolator; DO NOT automatically retry."""


class PurolatorSOAP:
    def __init__(self, config: AppConfig, wsdl_dir: str | Path,
                 *, client_factory: Callable[..., Any] | None = None):
        self.config = config
        self.dir = Path(wsdl_dir).expanduser()
        self._factory = client_factory or Client
        self._clients: dict[str, Any] = {}
        if config.environment not in ENV_HOSTS:
            raise CarrierConfigurationError("Unknown carrier environment")
        if not config.api_key or not config.api_password:
            raise CarrierConfigurationError("Purolator API key/password are not configured")
        if not self.dir.is_dir():
            raise CarrierConfigurationError("Private WSDL folder not found")

        session = requests.Session()
        session.auth = HTTPBasicAuth(config.api_key, config.api_password)
        session.verify = True
        self._session = session
        self._transport = Transport(session=session, timeout=15, operation_timeout=40)
        self._settings = Settings(
            strict=True, xml_huge_tree=False, forbid_dtd=True,
            forbid_entities=True, forbid_external=True, force_https=True,
        )

    def _load(self, service: str) -> Any:
        if service not in WSDL_FILES:
            raise CarrierConfigurationError("Unrecognized Purolator service")
        if service not in self._clients:
            filename, _ = WSDL_FILES[service]
            # Folder layout: private_wsdl/Development/{filename} or Production.
            directory = "Development" if self.config.environment == "development" else "Production"
            path = (self.dir / directory / filename).resolve()
            if not path.is_relative_to(self.dir.resolve()) or not path.is_file():
                raise CarrierConfigurationError(
                    f"Missing private {service} WSDL in {directory} folder"
                )
            try:
                client = self._factory(
                    str(path), transport=self._transport, settings=self._settings,
                )
            except Exception as exc:
                raise CarrierConfigurationError(
                    f"Unable to load {service} WSDL ({type(exc).__name__})"
                ) from None
            # Never accidentally transmit a Development request to Production.
            address = client.service._binding_options["address"]
            url = urlparse(address)
            if url.scheme != "https" or url.hostname != ENV_HOSTS[self.config.environment]:
                raise CarrierConfigurationError(
                    f"Endpoint host does not match {self.config.environment} environment"
                )
            self._clients[service] = client
        return self._clients[service]

    def call(self, service: str, operation: str, payload: dict[str, Any],
             *, reference: str | None = None, production_approved: bool = False) -> Any:
        """Explicitly validated call; no implicit production or retries.

        A timeout on CreateShipment is an UNKNOWN transaction outcome.
        The caller must mark needs_reconciliation in the SQLite journal.
        """
        if operation not in ALLOWED_OPERATIONS.get(service, frozenset()):
            raise CarrierConfigurationError("Unsupported Purolator operation")
        if not isinstance(payload, dict):
            raise CarrierConfigurationError("SOAP payload must be a named-field mapping")
        if self.config.environment == "production" and not production_approved:
            raise CarrierConfigurationError(
                "Production SOAP operation requires explicit operator approval"
            )
        client = self._load(service)
        version = WSDL_FILES[service][1]
        datatype_version = "v1" if service == "documents" else "v2"
        namespace = f"http://purolator.com/pws/datatypes/{datatype_version}"
        try:
            header = client.get_element(f"{{{namespace}}}RequestContext")(
                Version=version, Language="en", GroupID="Puro",
                RequestReference=reference or str(uuid4()),
            )
            method = getattr(client.service, operation)
            return method(_soapheaders=[header], **payload)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as exc:
            if operation == "CreateShipment":
                raise CarrierOutcomeUnknown(
                    "Carrier shipment result is unknown; reconcile before any retry"
                ) from None
            raise CarrierRequestError("Carrier connection failed") from None
        except Exception as exc:
            # SOAP faults and server failures after submission may be ambiguous.
            if operation == "CreateShipment":
                raise CarrierOutcomeUnknown(
                    "Carrier shipment result could not be confirmed; reconcile"
                ) from None
            raise CarrierRequestError(
                f"{operation} failed ({type(exc).__name__}); consult authorized logs"
            ) from None
