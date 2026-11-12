"""Generate a FICTIONAL SAP-style sales order export for testing Puro.

Matches the current approved layout in docs/SAP_EXPORT.md (single sheet,
6 columns: DocEntry, DocNum, CardCode, CustomerName, CustomerPO,
FullShipToAddress). All names and addresses are made up.

Usage (from the puro folder, Windows):
    .venv\\Scripts\\python.exe scripts\\make_test_orders.py
    .venv\\Scripts\\python.exe scripts\\make_test_orders.py --out test_data\\orders_test.xlsx
"""

import argparse
from pathlib import Path

import pandas as pd

ROWS = [
    # DocEntry, DocNum, CardCode, CustomerName, CustomerPO, FullShipToAddress
    (90001, 500001, "C90001", "Example Repairs Inc.", "PO-1001",
     "Example Repairs Inc., 12 Warehouse Lane, Ottawa ON K1A 0B1"),
    (90002, 500002, "C90002", "Sample Marine Supply", "PO-1002",
     "Sample Marine Supply, 45 Harbour Road, Kingston ON K7L 1A2"),
    (90003, 500003, "C90003", "Test Industrial Ltd.", "",
     "Test Industrial Ltd., 9 Mill Street Unit 3, Hamilton ON L8N 2B4"),
    (90004, 500004, "C90004", "Demo Energy Co.", "PO-1004",
     "Demo Energy Co., 200 Pine Avenue, Sudbury ON P3C 4T5"),
    (90005, 500005, "C90005", "Cash Sale", "",
     "John Doe 77 Maple Crescent, Barrie ON L4N 6H1"),
]

COLUMNS = ["DocEntry", "DocNum", "CardCode", "CustomerName",
           "CustomerPO", "FullShipToAddress"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="test_data/orders_test.xlsx",
                        help="output .xlsx path (default: test_data/orders_test.xlsx)")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(ROWS, columns=COLUMNS)
    # Keep identifiers as text so codes and postal values never lose leading zeros.
    df = df.astype({"CardCode": str, "CustomerPO": str, "FullShipToAddress": str})
    df.to_excel(out, sheet_name="Orders", index=False)

    print(f"Wrote {len(df)} fictional orders to {out.resolve()}")


if __name__ == "__main__":
    main()
