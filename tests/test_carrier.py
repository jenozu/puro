"""No network; fake SOAP client verifies Purolator boundaries."""
from pathlib import Path
import requests
import pytest

from puro.carrier import (
    CarrierConfigurationError, CarrierOutcomeUnknown,
    PurolatorSOAP,
)
from puro.config import AppConfig


class FakeService:
    def __init__(self, url, outcome="ok"):
        self._binding_options = {"address": url}
        self.outcome = outcome
        self.calls = []

    def __getattr__(self, name):
        def operation(*args, **kwargs):
            self.calls.append((name, kwargs))
            if self.outcome == "timeout":
                raise requests.exceptions.Timeout()
            return {"result": "fictional"}
        return operation


class FakeClient:
    def __init__(self, url):
        self.service = FakeService(url)
        self.header_calls = []

    def get_element(self, name):
        self.header_calls.append(name)
        return lambda **kw: kw


def config(environment="development"):
    return AppConfig(environment=environment, api_key="FICTIONALKEY",
                     api_password="FICTIONALPASSWORD")


def make_private_wsdl(tmp_path, environment="Development"):
    folder = tmp_path / environment
    folder.mkdir()
    (folder / "ShippingService.wsdl").write_text("<definitions/>")
    return tmp_path


def test_development_request_context_and_host(tmp_path):
    make_private_wsdl(tmp_path)
    fake = FakeClient("https://devwebservices.purolator.com/EWS/v2/Shipping/ShippingService.asmx")
    client = PurolatorSOAP(config(), tmp_path, client_factory=lambda *a, **kw: fake)
    reply = client.call("shipping", "ValidateShipment",
                        {"Shipment": {"test": "fictional"}}, reference="TEST-001")
    assert reply == {"result": "fictional"}
    assert fake.service.calls[0][0] == "ValidateShipment"
    assert fake.service.calls[0][1]["_soapheaders"][0]["Version"] == "2.2"
    assert fake.service.calls[0][1]["_soapheaders"][0]["RequestReference"] == "TEST-001"
    assert "datatypes/v2" in fake.header_calls[0]


def test_production_denied_without_approval(tmp_path):
    make_private_wsdl(tmp_path, "Production")
    fake = FakeClient("https://webservices.purolator.com/EWS/V2/Shipping/ShippingService.asmx")
    client = PurolatorSOAP(config("production"), tmp_path,
                          client_factory=lambda *a, **kw: fake)
    with pytest.raises(CarrierConfigurationError, match="approval"):
        client.call("shipping", "CreateShipment", {"Shipment": {}})
    assert not fake.service.calls


def test_dev_wsdl_mispointed_to_production_fails_closed(tmp_path):
    make_private_wsdl(tmp_path)
    fake = FakeClient("https://webservices.purolator.com/EWS/V2/Shipping/ShippingService.asmx")
    client = PurolatorSOAP(config(), tmp_path, client_factory=lambda *a, **kw: fake)
    with pytest.raises(CarrierConfigurationError, match="host"):
        client.call("shipping", "ValidateShipment", {"Shipment": {}})
    assert not fake.service.calls


def test_timeout_on_create_requires_reconciliation(tmp_path):
    make_private_wsdl(tmp_path)
    fake = FakeClient("https://devwebservices.purolator.com/EWS/V2/Shipping/ShippingService.asmx")
    fake.service.outcome = "timeout"
    client = PurolatorSOAP(config(), tmp_path, client_factory=lambda *a, **kw: fake)
    with pytest.raises(CarrierOutcomeUnknown, match="reconcile"):
        client.call("shipping", "CreateShipment", {"Shipment": {}})


def test_no_secret_in_error_on_missing_wsdl(tmp_path):
    client = PurolatorSOAP(config(), tmp_path)
    with pytest.raises(CarrierConfigurationError) as exc:
        client.call("shipping", "CreateShipment", {"Shipment": {}})
    assert "FICTIONALPASSWORD" not in str(exc.value)


def test_reject_unknown_methods_and_data(tmp_path):
    make_private_wsdl(tmp_path)
    client = PurolatorSOAP(config(), tmp_path)
    with pytest.raises(CarrierConfigurationError, match="Unsupported"):
        client.call("shipping", "RunAnyOperation", {})
    with pytest.raises(CarrierConfigurationError, match="mapping"):
        client.call("shipping", "ValidateShipment", "not a mapping")
