# Product Requirements Document — Puro Shipping Workstation

**Version:** 1.0 planning draft  
**Date:** 2026-10-27  
**Status:** requirements accepted for Task Master planning; shipping code not yet built  
**Repository:** https://github.com/jenozu/puro

## 1. Overview

Puro replaces the shipping portion of a legacy Streamlit/Purolator batch shipping application that required direct SQL Server access to SAP Business One. The replacement accepts an authorized daily SAP XLSX/CSV export, verifies order-specific **Ship To** data, constructs Purolator shipments via published SOAP services, and saves the resulting labels and tracking history locally.

The operator has active Purolator E-Ship SOAP Development and Production keys (portal screenshot), but activation does not prove a successfully authenticated live API call. Credentials are configured locally and never included in code, tests, screenshots or Git.

## 2. Users and jobs

**Primary persona:** Shipping coordinator working from a Windows PC with Excel exports, PDF printer and access to approved warehouse address/account. Needs to ship several orders quickly with minimal retyping and no duplicate charges.

**Secondary persona:** Maintainer/developer who reviews SOAP logs, updates mappings, tests Development endpoints, reconciles ambiguous API outcomes and maintains Task Master issues.

### Jobs-to-be-done
- "I have orders in SAP but no direct database access; let me upload export files."
- "The customer Bill To differs from where the order ships; ensure every label goes to Ship To."
- "I need to select batches of orders and confirm carton details."
- "I need Purolator's PIN, label PDF and traceable history even if the browser closes."
- "If the carrier times out, I need a safe resolution rather than duplicate shipment creation."

## 3. Goals, metrics and boundaries

**Goals**
1. Produce confirmed, printable domestic labels from a valid SAP export.
2. Avoid shipping to wrong addresses or creating duplicate chargeable shipments.
3. Allow safe recovery from network/Purolator errors without losing successful results.
4. Preserve a local audit trail and readable, re-downloadable labels.
5. Keep software workable without an SAP SQL connection.

**Proposed operational measures:** 100% correct Ship To in acceptance fixtures, zero duplicate create calls under click/retry tests, 100% successful shipment PINs saved to SQLite, 100% label-ready states backed by a saved verified PDF, 100% missing Ship To records blocked.

**MVP boundary:** Canadian domestic shipments, local single-operator workstation, manual PDF printing, Excel import. International shipping, customs, cross-carrier rate shopping, pickup scheduling, live SAP integration, ERP write-back and multiuser web hosting deferred.

## 4. Source data and identity

Required order header: `DocEntry`, `DocNum`, `CardCode`, `CustomerName`, ship-to street, city, province, postal code, country, plus approved contact as carrier requires. Optional: `ShipToCode`, customer PO, order status, order lines, customer phone, unit/building.

**Authoritative Ship To:** order-level `RDR12.StreetNoS`, `StreetS`, `BuildingS`, `Address2S`, `Address3S`, `CityS`, `StateS`, `ZipCodeS`, `CountryS`. Never use the `B` suffix billing address to populate the receiver.

Use `ORDR.DocEntry = RDR12.DocEntry`; use `RDR1.DocEntry` to join lines. Review orders that have multiple line-level destinations. If `RDR12` is unavailable, display any selected `CRD1` shipping address for explicit correction/approval; never auto-fill from Bill To. Maintain override provenance.

Details in [SAP_EXPORT.md](SAP_EXPORT.md).

## 5. Functional requirements

### FR-01: Import and mapping (P0)
- Accept .xlsx and .csv, discover worksheet `Orders` and optional `OrderLines`, support a known alias map for SAP column names.
- Preview imported records and show row-level errors before committing; no carrier calls during import.
- Reject invalid, ambiguous or missing Ship To data; preserve postal codes/identifiers as strings.
- Record source name, hash, import timestamp and resulting normalized order rows.
- Importing the same file or revised export may update unshipped order information but must not overwrite successfully shipped records or erase history.

### FR-02: Order queue (P0)
- Search by DocNum, customer and CardCode; select one or many for a batch.
- Present readiness warnings: missing address fields, line-level Ship To conflict, invalid contact, cancelled/closed status, already shipped or reconciliation pending.
- Distinguish source values from user overrides. Persist draft edits in SQLite.

### FR-03: Address review (P0 safety)
- Show order-specific Ship To, user-entered corrections and optional master shipping-address reference separately.
- Show a single final receiver preview mapping exactly to Purolator payload fields; verify name, street number/name, building/unit, city, province, postal/ZIP, country and phone.
- Never silently replace shipping fields with billing fields.
- User must explicitly confirm destination changes; block unresolved multi-destination orders.

### FR-04: Package/billing (P0)
- For each shipment: parcels array with positive weights and verified dimensions; explicit units conversions tested (lbs/inches UI vs SOAP units).
- Shipping service selected explicitly; if service availability integration is not ready, use a verified conservative service mapping and reject unsupported options.
- Support `Sender` (prepaid), `Receiver` (collect) and later `ThirdParty`; require valid billing account fields according to the WSDL. Default MVP to sender/receiver.
- Include sales order/customer PO references in the carrier reference field where documented; avoid accidental leakage of sensitive data into third-party fields.

### FR-05: Carrier validation (P0)
- Offline field validation runs before any network call.
- Purolator `ValidateShipment` when available and service selection/payer packaging consistency.
- Development environment default. Production requires clear UI indicator, revalidation and separate shipment-creation confirmation.
- Carrier-specific request/response types are WSDL-defined; SOAP request version and endpoints chosen by environment. Never hardcode credentials.

### FR-06: Shipment creation (P0 safety)
- Use `CreateShipment` only after passing final review. Send one request per intended shipment grouping, not once per order by assumption.
- Create durable attempt record before request, with unique internal correlation ID/reference and recorded non-sensitive request digest.
- Write shipment PIN, all piece PINs, creation timestamp, service, status, response metadata, last error and final destination.
- On definite rejection, mark failed with actionable error. On ambiguous outcome, mark `needs_reconciliation`; do not automatically resubmit.
- Guard against multiple UI reruns, double clicks and replay of already-created shipment.

### FR-07: Labels/documents (P0)
- Use `GetDocuments` after successful shipment; request PDF synchronously where service supports it. Handle documented URL/pending paths safely.
- Validate PDF bytes/type; write atomically under configured local labels directory; store file reference and checksum in SQLite.
- Distinguish `created`, `label_pending`, `label_ready` and `document_error`.
- Support per-label PDF download and batch ZIP of existing files without recreating shipments. Re-fetch a document only through an explicit safe action.

### FR-08: History, reconciliation and export (P0)
- Filter history by date, order, PIN, success/error; show each parcel's PIN.
- Export order/PIN/status CSV, no secrets and no labels in CSV.
- On unknown outcome, provide operator instructions to check Purolator by reference/PIN or customer support and record resolution decision.
- Support history surviving app/browser restart. For v0.1, voiding can be a documented manual operation; P1 add explicit `VoidShipment` UI.

### FR-09: Rate and service integration (P1)
- `GetFullEstimate` for account-specific estimates with an explicit service ID, currency, result validity and no stale estimates after any shipment edit.
- `GetServicesOptions`/service availability to filter permissible offerings; do not present a hardcoded service as automatically eligible.
- Estimates must be clearly marked estimates, not final charges.

### FR-10: Diagnostics, security and admin (P0)
- Display Development/Production account configuration state without exposing secret values.
- Connectivity checks must be non-chargeable where possible.
- Minimal structured logs with redaction: no API key/password, raw customer Excel data or full SOAP payload by default.
- Configurable data storage and label retention, local-only or appropriately authenticated network use.
- On startup, migrations/DB health checks; status of unavailable WSDL/local storage.

## 6. Non-functional requirements

**Reliability:** SQLite commits before and after non-idempotent requests; backup/recovery instructions. Atomic label writes. UI must not mark "Shipped and printable" until document is saved.

**Security:** no credentials committed to public repo or included in logs; local .env ignored; HTTPS/TLS to Purolator; certificate validation on; sanitized fixtures only; prevent unsafe path usage for output filenames; no execution of content from uploaded spreadsheets.

**Privacy:** data minimized to shipping purpose; configurable retention/deletion; backups protected. Reassess hosting/authentication if app moves from local workstation to multiuser/cloud.

**Usability:** Windows desktop browser; clear status indicators and inline errors; batch actions with selected counts; keyboard-friendly search and review; avoid unnecessary modal steps except Production final confirmation.

**Performance:** dozens of orders in a batch; progress per order. No concurrent unsafe resubmission of the same shipment. Long-running API requests time out predictably; errors don't discard other successes.

**Maintainability:** typed domain models, testable import mapping, carrier adapter behind interface, no carrier API calls in Streamlit UI code. WSDL versions/endpoints configurable with checked-in code-generated stubs only if distribution permissions allow; Purolator PDFs and proprietary bundles remain private.

## 7. Technical approach

- Python 3.11+, Streamlit, pandas + openpyxl, pydantic, zeep + requests, SQLAlchemy or sqlite3, pytest, optional PyMuPDF for later PDF merging.
- Repo planned layout:
  - `streamlit_app.py` UI entrypoint;
  - `puro/domain/models.py`, `validation.py`;
  - `puro/importers/sap_excel.py`, `column_map.py`;
  - `puro/carriers/purolator_soap.py`, `schemas.py`;
  - `puro/storage/db.py`, `repositories.py`;
  - `tests/unit/`, `tests/contracts/`, `tests/fixtures/synthetic/`;
  - `.taskmaster/` machine-readable planning docs/tasks.
- Local .env with `PUROLATOR_ENV`, API key/password, account and sender fields; development default.
- Documented SOAP versions: Shipping 2.2; Get Documents 1.4; Estimates 2.2; Service Availability 2.0. The uploaded technical guides are service versions 2.2.2/1.4.0/2.2.2/2.0.4 respectively; verify actual WSDL at implementation time.
- Deployment: local Windows venv, launch PowerShell/batch helper after app implemented; retain no SQL Server ODBC dependency.

## 8. Core data model

`imports`: id, file name/checksum/time, schema/result summary.  
`orders`: id, DocEntry, DocNum, CardCode, source snapshot, ship-to source/override, review status, import reference.  
`shipments`: internal id, grouping key, order id, environment/account fingerprint (not credential), status, service, payer, sender/receiver snapshot, created timestamp, shipment PIN, error code/detail.  
`shipment_pieces`: shipment id, piece ordinal, package dimensions/weight, piece PIN.  
`shipment_attempts`: id, shipment id, correlation id, started/completed, request hash, outcome classification, carrier reference.  
`documents`: shipment/piece id, type, file path, hash, status, retrieved date.  
`audit_events`: operator action, timestamp, old/new status, redacted context.

A uniqueness constraint and service-level transaction guard must stop resubmission after `creating`, `created`, `label_pending`, `label_ready` or `needs_reconciliation`, except through a controlled reconciliation workflow.

## 9. Required test cases / acceptance criteria

- **AT-01**: synthetic Ship To ≠ Bill To; generated receiver SOAP fields exactly equal Ship To, never billing.
- **AT-02**: missing street/city/zip/country; app prevents carrier creation.
- **AT-03**: one order with two distinct line Ship To codes; app blocks single-recipient shipment.
- **AT-04**: duplicate import; preserved shipped records and PDFs.
- **AT-05**: double click/concurrent UI rerun/restart; at most one `CreateShipment` attempt per authorized shipment.
- **AT-06**: timeout immediately after request; `needs_reconciliation`, not automatic retry.
- **AT-07**: sender and receiver payment payloads, missing receiver account blocked.
- **AT-08**: multiple parcels; all piece PINs retained and labels verifiable.
- **AT-09**: returned document pending/error; app does not claim label ready.
- **AT-10**: successful label retrieval: valid PDF bytes saved atomically and downloadable after restart.
- **AT-11**: isolation: Development cannot invoke Production endpoint; switch requires explicit operator confirmation.
- **AT-12**: config/logging never emits credential values; no secrets committed.
- **AT-13**: mock/unit test suite green; approved Development test and one supervised live Production test manually signed off.

## 10. Release milestones and dependencies

**M0 — Docs and planning:** README, PRD, MVP, SAP contract, security, Task Master backlog (this commit series).

**M1 — Foundations:** code skeleton, settings, local DB migrations, logging, pytest, representative synthetic fixtures.

**M2 — Import:** XLSX/CSV normalizer, source address selection, order queue, persistent edits and validation.

**M3 — Purolator adapter:** SOAP WSDLs, environment config, request/response models, validation, creation, label retrieval with mocked fixtures and development tests.

**M4 — End-to-end batch safety:** durable attempts, duplicate guards, reconciliation, piece labels, downloadable PDFs/history.

**M5 — Controlled rollout:** test matrix, printer setup, operational runbook, explicit authorized production sign-off.

**M6 — Enhancements:** rate estimates, service availability, third-party billing, voids/manifests, scanner/printing improvements and eventually US/international.

See [BUILD_PLAN.md](BUILD_PLAN.md) for execution order. Task Master parses `.taskmaster/docs/prd.txt` and uses `.taskmaster/tasks/tasks.json` as its seeded backlog.

## 11. Risks, questions and decisions

| Risk | Mitigation |
| --- | --- |
| RDR12 field missing from spreadsheet | stop processing; clear mapping error; explicit manual correction |
| Old app source imports unavailable modules | reimplement client from supplied Purolator WSDL, not assume existing behavior |
| WSDL differs from current production | validate WSDL endpoints and SOAP contract against authorized Development environment |
| Multiple packages / destinations | explicit shipment grouping and piece model; refuse ambiguous input |
| Carrier request accepted but response lost | journal before call and reconciliation status |
| Repo is public | no private docs, credentials, customer data, or actual labels |
| Production key active but account not authorized for selected service | verify service eligibility/test before shipment |
| Certification/business rules | ask Purolator where applicable; do not assume certification automatically waived |
| No concrete real export or sender/label-printer specs yet | continue with synthetic imports/tests; collect these before live sign-off |

## 12. Constraints for AI/Task Master implementers

- Implement tasks according to dependency order in the existing Task Master backlog.
- Do not invent Purolator WSDL field names or assume amounts/service eligibility. Use the supplied official WSDLs locally; if absent, stub and mark verification pending.
- Keep customer data and API credentials out of AI prompts/commits.
- Never execute live Production shipment calls in tests/CI.
- A completed code task must include tests, documentation updates, and explicit user acceptance where carrier billing or output is involved.
