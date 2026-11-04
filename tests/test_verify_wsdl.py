"""Mechanics test for scripts/verify_wsdl_wrappers.py.

The WSDL below is a tiny FICTIONAL fixture shaped like the field names Puro sends.
It is NOT the Purolator schema: the real check runs on the operator's private WSDLs.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_wsdl_wrappers.py"
spec = importlib.util.spec_from_file_location("verify_wsdl_wrappers", SCRIPT)
verify_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_mod)

DT = "http://purolator.com/pws/datatypes/v2"


def _wsdl(*, host="devwebservices.purolator.com", city_name="City") -> str:
    def f(n, t="xs:string", opt=False):
        return f'<xs:element name="{n}" type="{t}"' + (' minOccurs="0"' if opt else "") + "/>"

    def ct(name, *items):
        return f'<xs:complexType name="{name}"><xs:sequence>{"".join(items)}</xs:sequence></xs:complexType>'

    types = "".join([
        ct("Phone", f("CountryCode"), f("AreaCode"), f("Phone")),
        ct("Address", f("Name"), f("StreetNumber"), f("StreetName"), f("Suite", opt=True),
           f("StreetAddress2", opt=True), f("StreetAddress3", opt=True), f(city_name),
           f("Province"), f("Country"), f("PostalCode"), f("PhoneNumber", "dt:Phone")),
        ct("Party", f("Address", "dt:Address")),
        ct("Weight", f("Value", "xs:decimal"), f("WeightUnit")),
        ct("Length", f("Value", "xs:decimal"), f("DimensionUnit")),
        ct("Piece", f("Weight", "dt:Weight"), f("Length", "dt:Length"),
           f("Width", "dt:Length"), f("Height", "dt:Length")),
        ct("Pieces", '<xs:element name="Piece" type="dt:Piece" maxOccurs="unbounded"/>'),
        ct("Package", f("ServiceID"), f("TotalWeight", "dt:Weight"), f("TotalPieces", "xs:int"),
           f("PiecesInformation", "dt:Pieces")),
        ct("Payment", f("PaymentType"), f("RegisteredAccountNumber"), f("BillingAccountNumber", opt=True)),
        ct("Pickup", f("PickupType")),
        ct("Tracking", f("Reference1")),
        ct("Shipment", f("SenderInformation", "dt:Party"), f("ReceiverInformation", "dt:Party"),
           f("PackageInformation", "dt:Package"), f("PaymentInformation", "dt:Payment"),
           f("PickupInformation", "dt:Pickup"), f("TrackingReferenceInformation", "dt:Tracking", opt=True)),
        ct("Err", f("Code")),
        ct("Errs", '<xs:element name="Error" type="dt:Err" maxOccurs="unbounded"/>'),
        ct("RespInfo", f("Errors", "dt:Errs", opt=True)),
        ct("PIN", f("Value")),
        ct("PINs", '<xs:element name="PIN" type="dt:PIN" maxOccurs="unbounded"/>'),
        '<xs:element name="RequestContext"><xs:complexType><xs:sequence>'
        + f("Version") + f("Language") + f("GroupID") + f("RequestReference") + "</xs:sequence></xs:complexType></xs:element>",
        '<xs:element name="ValidateShipmentRequest"><xs:complexType><xs:sequence>' + f("Shipment", "dt:Shipment")
        + "</xs:sequence></xs:complexType></xs:element>",
        '<xs:element name="ValidateShipmentResponse"><xs:complexType><xs:sequence>'
        + f("ResponseInformation", "dt:RespInfo") + "</xs:sequence></xs:complexType></xs:element>",
        '<xs:element name="CreateShipmentRequest"><xs:complexType><xs:sequence>' + f("Shipment", "dt:Shipment")
        + f("PrinterType") + "</xs:sequence></xs:complexType></xs:element>",
        '<xs:element name="CreateShipmentResponse"><xs:complexType><xs:sequence>'
        + f("ResponseInformation", "dt:RespInfo") + f("ShipmentPIN", "dt:PIN") + f("PiecePINs", "dt:PINs")
        + "</xs:sequence></xs:complexType></xs:element>",
    ])
    ops = ""
    for op in ("ValidateShipment", "CreateShipment"):
        ops += (f'<operation name="{op}"><input message="tns:{op}In"/><output message="tns:{op}Out"/></operation>')
    msgs = "".join(
        f'<message name="{op}{d}"><part name="p" element="dt:{op}{r}"/></message>'
        for op in ("ValidateShipment", "CreateShipment") for d, r in (("In", "Request"), ("Out", "Response"))
    ) + '<message name="Ctx"><part name="h" element="dt:RequestContext"/></message>'
    bind_ops = "".join(
        f'<operation name="{op}"><soap:operation soapAction="{op}"/>'
        f'<input><soap:body use="literal"/><soap:header message="tns:Ctx" part="h" use="literal"/></input>'
        f'<output><soap:body use="literal"/></output></operation>'
        for op in ("ValidateShipment", "CreateShipment")
    )
    return f"""<?xml version="1.0"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
 xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:tns="http://purolator.com/pws/service/v2"
 xmlns:dt="{DT}" targetNamespace="http://purolator.com/pws/service/v2">
<types><xs:schema targetNamespace="{DT}" elementFormDefault="qualified">{types}</xs:schema></types>
{msgs}
<portType name="PT">{ops}</portType>
<binding name="B" type="tns:PT"><soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>{bind_ops}</binding>
<service name="S"><port name="P" binding="tns:B"><soap:address location="https://{host}/EWS/V2/Shipping/ShippingService.asmx"/></port></service>
</definitions>"""


def _run(tmp_path, wsdl_text, capsys):
    folder = tmp_path / "Development"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ShippingService.wsdl").write_text(wsdl_text)
    report = verify_mod.Report()
    verify_mod.verify(tmp_path, ("shipping",), tmp_path / "dump", report)
    return report, capsys.readouterr().out


def test_matching_wsdl_passes_and_dumps_unsent_envelopes(tmp_path, capsys):
    report, out = _run(tmp_path, _wsdl(), capsys)
    assert report.failures == 0, out
    assert "request serializes against WSDL" in out
    assert (tmp_path / "dump" / "shipping.CreateShipment.request.xml").is_file()


def test_field_mismatch_is_reported_not_sent(tmp_path, capsys):
    report, out = _run(tmp_path, _wsdl(city_name="Town"), capsys)
    assert report.failures >= 2, out  # Validate + Create both fail to serialize
    assert "[FAIL] shipping.CreateShipment: request serializes" in out


def test_wrong_endpoint_host_is_flagged(tmp_path, capsys):
    report, out = _run(tmp_path, _wsdl(host="webservices.purolator.com"), capsys)
    assert report.failures >= 1
    assert "[FAIL] shipping: endpoint is https://devwebservices.purolator.com" in out
