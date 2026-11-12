"""Read-only field tree for one WSDL request element. Sends NOTHING.

Prints the child element names the WSDL schema expects under a request
element (for example CreateShipmentRequest), following type references
that are defined inside the same WSDL file. Local files only.

Usage (from the puro folder, Windows):
    .venv\\Scripts\\python.exe scripts\\check_wsdl_fields.py "<private folder>\\ShippingService.wsdl" CreateShipmentRequest

Paste the printed output back. It contains element and type names only.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

XS = "{http://www.w3.org/2001/XMLSchema}"
MAX_DEPTH = 6


def local(name: str) -> str:
    return name.split(":")[-1].split("}")[-1]


def build_index(root: ET.Element) -> tuple[dict, dict]:
    elements: dict[str, ET.Element] = {}
    types: dict[str, ET.Element] = {}
    for node in root.iter():
        if node.tag == f"{XS}element" and "name" in node.attrib:
            elements.setdefault(node.attrib["name"], node)
        elif node.tag in (f"{XS}complexType", f"{XS}simpleType") and "name" in node.attrib:
            types.setdefault(node.attrib["name"], node)
    return elements, types


def print_children(node: ET.Element, types: dict, depth: int) -> None:
    if depth > MAX_DEPTH:
        return
    for child in node.iter():
        if child.tag == f"{XS}element" and "name" in child.attrib:
            indent = "  " * depth
            ref_type = local(child.attrib.get("type", ""))
            suffix = f"  [{ref_type}]" if ref_type else ""
            print(f"{indent}{child.attrib['name']}{suffix}")
            if ref_type in types and depth < MAX_DEPTH:
                print_children(types[ref_type], types, depth + 1)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: check_wsdl_fields.py <file.wsdl> <RequestElementName>")
    path, target = Path(sys.argv[1]), sys.argv[2]
    root = ET.parse(path).getroot()
    elements, types = build_index(root)
    if target not in elements:
        sys.exit(f"Element '{target}' not found in {path.name}")
    print(f"=== {target} (from {path.name}) ===")
    print_children(elements[target], types, 1)


if __name__ == "__main__":
    main()
