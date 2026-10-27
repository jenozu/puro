"""Shipping address invariant, independent of any customer billing data."""
import pytest

from puro.address import AddressProblem, recipient_snapshot, shipping_errors


def address():
    return {
        "ship_to_name": "Receiving Dock",
        "street_no": "88", "street": "Warehouse Street",
        "city": "Ottawa", "province": "on",
        "postal_code": "k1a0b1", "country": "ca",
        "building": "Unit 3", "address2": "Rear dock",
        "address3": "Gate C", "phone": "6135550000",
        # Deliberate real-looking billing fields must never enter payload.
        "StreetB": "99 Accounts Avenue", "CityB": "Toronto",
        "ZipCodeB": "M5V1A1", "BillToStreet": "99 Accounts Avenue",
        "CustomerName": "Accounts Company",
    }


def test_ship_to_exclusive_and_normalized():
    snap = recipient_snapshot(address())
    assert snap["street"] == "Warehouse Street"
    assert snap["city"] == "Ottawa"
    assert snap["name"] == "Receiving Dock"
    assert snap["postal_code"] == "K1A 0B1"
    assert snap["province"] == "ON"
    assert snap["building"] == "Unit 3"
    assert "99 Accounts Avenue" not in str(snap)
    assert "Toronto" not in str(snap)


def test_missing_destination_fails_closed_not_bill_to_fallback():
    data = address()
    data["street"] = ""
    with pytest.raises(AddressProblem, match="Missing Ship To street"):
        recipient_snapshot(data)


def test_multiple_line_destinations_cannot_generate_recipient():
    data = address()
    data["line_conflict"] = True
    with pytest.raises(AddressProblem, match="Line-level Ship To conflict"):
        recipient_snapshot(data)


@pytest.mark.parametrize("street", ["TBD", "Same as Bill To", "N/A", "UNKNOWN"])
def test_placeholders_blocked(street):
    data = address()
    data["street"] = street
    assert any("placeholder" in x for x in shipping_errors(data))


def test_bad_postal_and_province_blocked():
    data = address()
    data.update(province="ZZ", postal_code="D0 D0 D0")
    errs = shipping_errors(data)
    assert any("province" in x for x in errs)
    assert any("postal code" in x for x in errs)


def test_invalid_country_rejected():
    data = address()
    data["country"] = "US"
    with pytest.raises(AddressProblem, match="Canadian destinations"):
        recipient_snapshot(data)
