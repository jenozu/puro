# MVP Specification — Puro v0.1

**Status:** approved proposal / not yet implemented  
**Primary use case:** domestic Canadian order shipping with SAP Business One exports and Purolator E-Ship SOAP  
**Owner:** Shipping operations  
**Target deployment:** local Windows workstation, one active operator; no SAP credentials or direct database access

## Problem and outcome

Shipping staff lost direct SAP access. The prior Streamlit prototype retrieved SAP sales orders through SQL Server/ODBC and created shipments via a separate, now-missing Purolator client. Staff need to export shipping orders to Excel and create/print correct Purolator labels without retyping addresses.

Success means: import order data, search/select order(s), show and confirm the **order-specific Ship To**, set package and payer details, validate, explicitly create a shipment, save the resulting label(s), and persist the tracking history. Every actual carrier transaction must be attributable to a specific order/package.

## Included in v0.1

1. Local Streamlit interface with Import, Orders, Review/Ship, History, Settings/Diagnostics.
2. XLSX + CSV import; supported schema: `Orders` sheet containing order-level `ORDR`/`RDR12` joined data. An optional `OrderLines` sheet supports warnings about multiple line-level Ship To values. Clear errors when files or columns are missing.
3. Normalize `DocEntry`, `DocNum`, `CardCode`, recipient/contact fields, country/province/postal code and *shipping* address. Never auto-substitute Bill To.
4. Editable per-shipment destination, sender, packaging (weight/dimensions per parcel), service and prepaid/collect billing. Require user confirmation of manually changed addresses.
5. Explicit **Development / Production** environment setting with prominent environment indicator; default development. Production mode requires a confirm step immediately before submission.
6. WSDL-driven Python SOAP client using `zeep` and explicit `RequestContext` version values for Shipping v2.2 and Get Documents v1.4. Service names and field semantics are verified against supplied Purolator definitions.
7. Preflight local validation and carrier `ValidateShipment` where available; never assume validation prevents all shipping errors.
8. `CreateShipment` and `GetDocuments` with direct PDF retrieval or documented supported fallback; persist returned shipment and piece PINs; verify document response before displaying Ready to Print.
9. Local SQLite shipment journal and local PDF storage with atomic writes. Prevent accidental resends; unknown API outcomes become `needs_reconciliation`.
10. Download individual PDF labels, batch ZIP of existing labels, history CSV, and per-order actionable errors.
11. Automated unit/contract tests using sanitized fixtures/mocks; no real API calls during routine tests.

## Not included in the first shipping release

- Full international/customs workflows, commercial invoices, duty/tax scenarios or dangerous goods.
- Carrier pickup scheduling and tracking polling.
- Automated service/rate optimization or carrier comparisons.
- Direct SAP/SQL connection, ERP write-back, and customer master syncing.
- Multiuser simultaneous shipment creation or internet-hosted deployment.
- Automated thermal printing / ZPL commands (PDF printing first); void/manifest UI optional next increment.
- Inferring actual packed weight from SAP item-master weights.

## Preconditions and outstanding operational inputs

- Authorized, active Development and Production Purolator keys (reported active in portal; **no credential values stored here**).
- Warehouse/sender name, exact street, city, province, postal code, phone and account.
- Real, authorized SAP export with at least one known Ship To ≠ Bill To case (test fixtures should be anonymized).
- Destination sample(s), package sizes, PDF label printer/page or stock preference.
- Explicit approval before real production shipping, even if credentials are active.

## Safety-critical rules

- **Source precedence:** Order-specific `RDR12.*S` address is authoritative. Address book `CRD1` may be displayed only as secondary reference with operator approval; never use `*B` billing fields as destination.
- All statuses: `imported`, `needs_review`, `ready`, `creating`, `needs_reconciliation`, `created`, `label_pending`, `label_ready`, `failed`, `voided`.
- Uniqueness: shipment attempt belongs to a canonical `DocEntry` + destination + parcel grouping + operator-authorized batch record; once creating/created/reconciliation is present, resubmission is blocked until safely resolved.
- Timeouts, disconnections and truncated responses after shipment submission **do not** mean that Purolator did not create it.
- No blind automatic retry of non-idempotent `CreateShipment`; provide manual reconciliation using returned PINs, shipment references or carrier support.
- PDF label success is separate from shipment success. Store all piece PINs, label bytes and retrieval metadata.
- An import must never overwrite existing successful shipment records.

## User workflow (happy path)

1. Start app with local `.env` and production kept disabled.
2. Upload `SAP_Shipping_Export.xlsx`, map columns if necessary, review import errors.
3. Select open unshipped sales orders by `DocNum`, `CardCode` or customer.
4. Verify receiver Ship To, recipient name/phone, package count, actual weight/dimensions, shipping service and payer.
5. Run preflight validation; inspect shipment summary.
6. Select Development or explicitly unlock Production and confirm a specific batch.
7. Create each shipment once, persist PINs and retrieve/save label PDFs.
8. Download/print and verify destination, service, barcodes and per-parcel labels.
9. Reopen app; confirm order history and label re-download work without recreating shipment.

## Release acceptance gates

| ID | Scenario | Expected |
| --- | --- | --- |
| A01 | Ship To and Bill To differ | Label request contains order Ship To; billing destination is never copied |
| A02 | Missing/ambiguous `RDR12` | Block shipment; user sees exact fields to correct |
| A03 | Reimport same workbook | Existing shipment journal and saved PDFs remain |
| A04 | Duplicate click or rerun | No duplicate `CreateShipment` request |
| A05 | Network timeout after submission | `needs_reconciliation`, no auto-retry |
| A06 | Two parcels | Piece PINs and corresponding documents persist |
| A07 | Invalid weight / billing account | Block before live call |
| A08 | Carrier rejects request | Order-level error; other batch orders preserved |
| A09 | Documents pending/error | Not marked ready; subsequent retrieval controlled |
| A10 | Restart app | History, statuses and labels survive |
| A11 | Normal automated tests | All mocked tests pass, no external requests |
| A12 | Real authorized development test | Shipment + correct printable PDF verified |
| A13 | Controlled production test | Approved account charged intentionally, label reviewed, duplicate/void procedure documented |

## Release decision

Do not mark MVP Production Ready until gates A01–A13 pass and the operator validates an authorized live test. A functioning UI alone is **not** proof of a shipping-ready application. Related details: [PRD](PRD.md), [build plan](BUILD_PLAN.md), [SAP export](SAP_EXPORT.md).
