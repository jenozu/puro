"""Read-only WSDL summary for Puro. Sends NOTHING to Purolator.

Lists, for each WSDL file: the service endpoint addresses, and every
operation with its input and output element names. It reads only the
local files and does not follow any imports or network links.

Usage (from the puro folder, Windows):
    .venv\\Scripts\\python.exe scripts\\check_wsdl_ops.py "<private folder holding the Development .wsdl files>"

Paste the printed output back. It contains operation and element names
and endpoint hosts only, no credentials or customer data.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "wsdl": "http://schemas.xmlsoap.org/wsdl/",
    "soap": "http://schemas.xmlsoap.org/wsdl/soap/",
    "soap12": "http://schemas.xmlsoap.org/wsdl/soap12/",
}


def local(name: str) -> str:
    return name.split("}", 1)[-1] if "}" in name else name


def summarize(path: Path) -> None:
    print(f"\n=== {path.name} ===")
    root = ET.parse(path).getroot()

    # Endpoint addresses
    for addr in root.iter():
        if local(addr.tag) == "address" and "location" in addr.attrib:
            print(f"  endpoint: {addr.attrib['location']}")

    # Messages: name -> element names of their parts
    messages = {}
    for msg in root.findall("wsdl:message", NS):
        parts = []
        for part in msg.findall("wsdl:part", NS):
            parts.append(part.attrib.get("element") or part.attrib.get("type") or "?")
        messages[msg.attrib["name"]] = parts

    # Port types: operation -> input/output message
    for pt in root.findall("wsdl:portType", NS):
        print(f"  portType: {pt.attrib['name']}")
        for op in pt.findall("wsdl:operation", NS):
            inp = op.find("wsdl:input", NS)
            out = op.find("wsdl:output", NS)
            in_msg = inp.attrib.get("message", "") if inp is not None else ""
            out_msg = out.attrib.get("message", "") if out is not None else ""
            in_parts = messages.get(in_msg.split(":")[-1], [])
            out_parts = messages.get(out_msg.split(":")[-1], [])
            print(f"    - {op.attrib['name']}")
            print(f"        input : {', '.join(in_parts) or '?'}")
            print(f"        output: {', '.join(out_parts) or '?'}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: check_wsdl_ops.py <folder containing the .wsdl files>")
    folder = Path(sys.argv[1])
    files = sorted(folder.glob("*.wsdl"))
    if not files:
        sys.exit(f"No .wsdl files found in {folder}")
    for f in files:
        summarize(f)


if __name__ == "__main__":
    main()
