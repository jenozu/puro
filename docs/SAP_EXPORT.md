# SAP Business One Export Contract

This specification describes a data contract, **not** a tested SQL query. SAP SQL tables/columns must be verified on the authorized company's SQL Server system. Existing schema screenshots show order-level Ship To and Bill To are distinct.

## Preferred input

An XLSX workbook with worksheet `Orders` (one row per open order / destination), and optionally `OrderLines`. CSV with equivalent flattened fields is also accepted. Exact matching should be case-insensitive and allow recognized SAP aliases, but reject ambiguous duplicate mappings.

### Orders worksheet

| Canonical field | SAP source | Priority |
| --- | --- | --- |
| `DocEntry` | `ORDR.DocEntry` | Required for reliable identity |
| `DocNum` | `ORDR.DocNum` | Required to search orders |
| `CardCode` | `ORDR.CardCode` | Required |
| `CustomerName` | `ORDR.CardName` | Required display |
| `CustomerPO` | `ORDR.NumAtCard` | Optional |
| `DocStatus`, `Canceled` | `ORDR.DocStatus`, `ORDR.CANCELED` | Recommended for filtering |
| `ShipToCode` | `ORDR.ShipToCode` | Recommended |
| `ShipToStreetNo` | `RDR12.StreetNoS` | Required where numbered |
| `ShipToStreet` | `RDR12.StreetS` | Required |
| `ShipToBuilding` | `RDR12.BuildingS` | Optional |
| `ShipToAddress2` | `RDR12.Address2S` | Optional |
| `ShipToAddress3` | `RDR12.Address3S` | Optional |
| `ShipToCity` | `RDR12.CityS` | Required |
| `ShipToProvince` | `RDR12.StateS` | Required for Canadian domestic shipping |
| `ShipToPostalCode` | `RDR12.ZipCodeS` | Required |
| `ShipToCountry` | `RDR12.CountryS` | Required |
| `ContactPhone` | Verified contact (e.g., `OCRD.Phone1`) | Required if dictated by carrier; editable |
| `ShipToName` | Verified shipping recipient / address name | Preferred; editable |
| `ShippingMethod` | `OSHP.TrnspName` | Optional hint, not trusted as a carrier service ID |

`Address` free text from `ORDR.Address2` can be retained for visual comparison, **not** preferred over structured `RDR12` fields. No `StreetB`, `CityB`, `ZipCodeB`, or other billing suffix `B` is eligible for destination mapping.

### OrderLines worksheet (optional but recommended)

`DocEntry`, `LineNum`, `ItemCode`, `ItemDescription`, `OpenQuantity`, `LineShipToCode`, optional item weight/dimension reference. Join by `DocEntry` (not `DocNum`). If more than one distinct line Ship To is present, block automatic single-destination shipment generation and require a manually approved split.

## Import validations

- Excel strings may contain leading zeros; do not coerce codes/zip/postal values to floats or scientific notation.
- Identifiers are normalized as strings for display; `DocEntry` is a database identity, `DocNum` is a displayed document number.
- Filter cancelled/closed lines where available; unknown status requires review.
- Validate Canadian postal code patterns and country/province; **do not** assume postal-code shape proves delivery feasibility.
- Do not fabricate street numbers or split them heuristically without review (e.g., PO boxes and rural routes).
- Keep name, contact, building/unit and street lines separate as long as possible; confirm the final SOAP receiver payload is faithful.
- Fail clearly when Ship To is blank, incomplete, or mapped to billing fields.
- Retain import provenance (filename, time, checksum, source row) and audit field overrides.
- A customer-master `CRD1` selected Ship To address is reference only. Operator-approved fallback is allowed only if the order-specific address cannot be used. Never fall back to the billing address.
- Reimports are idempotent for source records; **must not reset shipment status**.

## SQL export notes

Source query should use `ORDR O LEFT JOIN RDR12 A ON O.DocEntry=A.DocEntry`, `OCRD` for contact, and optional `OSHP`. Apply `O.CANCELED='N'` and `O.DocStatus='O'` when shipping open orders. Verify source SQL dialect and actual available fields with SAP admin. No direct SQL access is needed from Puro itself.

If sharing a query with the operator, break the **single query** into the user's four requested windows: 1. `SELECT`, 2. `FROM/JOIN`, 3. `WHERE`, 4. `ORDER BY`.

## Test examples (fictional)

A test order for company `Example Repairs Inc.` can have Ship To `12 Warehouse Lane, Ottawa, ON` while the Bill To is `88 Accounts Avenue, Toronto, ON`. Assertions must verify that *all* receiver street/city/province/postal fields match the Ship To; Bill To values must never appear in the receiver payload.
