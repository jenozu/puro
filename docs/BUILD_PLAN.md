# Implementation Plan — Puro v2 (Task Master managed)

**Date:** 2026-10-27  
**Principle:** prioritize **correct destination, non-duplicate transactions, labels saved** over visual polish.  
**Scope:** internal local-first Canadian domestic MVP; production never called by automated tests.

## Current technical audit

Prior prototype: ~873-line Streamlit module. The UI is useful but imports unavailable `config.settings`, `data.sap_repository`, `services.purolator_client` and `utils.helpers`. Previous README requires SQL/ODBC; history in Streamlit session; fixed package defaults; successful batch orders may remain in queue after partial failure; address logic unverified. Do **not** use original code as proof of functional SOAP request handling.

Purolator WSDL bundles supplied in private project materials: Shipping 2.2.2, Get Documents 1.4.0, Estimates 2.2.2, Service Availability 2.0.4 (Development & Production definitions). These enable contract-driven development. The user reports active developer and production API keys; no credentials have been used/tested by this project. This repository is PUBLIC and must never contain the proprietary original documentation or operational data.

## Phase sequence

### P0. Repository/scaffolding and Task Master
- Docs: README, MVP, PRD, SAP contract, security, build plan, `.taskmaster/docs/prd.txt`, `.taskmaster/tasks/tasks.json`.
- Task Master Cursor MCP config and workflows; model provider configured locally by developer; no AI keys in repo.
- Define CI lint/unit test policy, synthetic fixtures, secret scan and coverage gate.
- **Exit:** `task-master list` can read seeded tasks once Task Master is installed/configured; docs complete.

### P1. Foundation and configuration
- Build Python `puro/` package + `streamlit_app.py`, dependencies, type-checked settings, deterministic logging, local data directory and SQLite migrations.
- Environment defaults to Development. Startup redacts secrets and describes missing settings without exposing values.
- **Exit:** local app opens, SQLite persists state after restart; configuration errors tested.

### P2. Import and address correctness
- XLSX/CSV parser; alias map; row/column validation; import records and source provenance.
- Canonical order + `RDR12` Ship To models; enforce complete receiver fields; no automatic Bill To fallback.
- UI order search/selection and draft address editing; separate source vs overrides.
- Optional `OrderLines` line-Ship-To conflict warning.
- **Exit:** test shipment preview uses order-specific Ship To when billing and shipping differ; repeat imports do not overwrite shipped records.

### P3. Carrier adapter + mocked contracts
- `zeep` SOAP client, versions and RequestContext; separate Development/Production WSDL/endpoints and credentials.
- `ValidateShipment`, `CreateShipment`, `GetDocuments` with synthetic mocked transport; robust error/status decoding.
- Unit conversions, receiver/paid-by/piece payload mapping, per-request correlation and redacted logging.
- **Exit:** all contract fixtures pass, including receiver billing, multi-piece responses and pending documents. No production requests.

### P4. Durable transaction/state machine
- SQLite attempt record before network request; state progression ready → creating → created → label_pending/ready, or failure/reconciliation.
- Uniqueness/idempotency guard prevents double submission. Unknown outcome = needs_reconciliation, never blind retries.
- Persist piece PINs, locally saved PDF and checksum; batch progress and CSV results.
- **Exit:** timeout and browser restart tests don't produce duplicate creation; saved labels still accessible.

### P5. Controlled real-environment verification
- Confirm sender, account entitlements, service IDs, labels and printer stock.
- Test Development against actual WSDL with non-chargeable test data and inspect valid PDF output.
- Review Purolator certification/account requirements; do not assume production entitlement merely from portal status.
- Operator authorizes **one** production trial deliberately, verifies destination, parcel count, printed/scannable labels, charge and reconciliation process; document signoff.
- **Exit:** every [MVP acceptance gate](MVP.md) passed, explicitly authorized for operational use.

### P6. After MVP
- Estimates and service availability, third-party billing, operator void UI, end-of-day manifests, saved package presets and thermal labels. International/customs separate release.

## Proposed repo layout (implementation tasks will create)

```text
README.md
docs/PRD.md docs/MVP.md docs/BUILD_PLAN.md docs/SAP_EXPORT.md docs/SECURITY.md docs/TASKMASTER.md
.taskmaster/docs/prd.txt .taskmaster/tasks/tasks.json
.cursor/mcp.json
.env.example .gitignore
streamlit_app.py                # planned, not yet present
requirements.txt                # planned, not yet present
puro/
  config.py
  domain/{models,validation}.py
  importers/{sap_excel,column_map}.py
  carriers/{purolator_soap,payloads}.py
  storage/{db,repositories}.py
tests/{unit,contracts,fixtures}/
```

## Priority and dependency mapping (mirrored in Task Master)

| Task ID | Task | Requires | Acceptance |
| --- | --- | --- | --- |
| 1 | Project skeleton/config/test scaffolding | None | App starts; secrets redacted |
| 2 | SQLite schema/journal | 1 | Restart-safe state |
| 3 | Excel/CSV importer + mapping | 1,2 | Correct DocEntry/source provenance |
| 4 | Ship To normalization/validation | 3 | Ship To ≠ Bill To test |
| 5 | Orders/review UI | 2,3,4 | Correct preview, user confirmation |
| 6 | Purolator SOAP adapter/contracts | 1 | WSDL/mock tests |
| 7 | Shipment validation/payer/packages | 4,6 | Reject invalid shipment |
| 8 | Safe shipment creation/journal | 2,5,6,7 | Duplicate/timeouts protected |
| 9 | Get Documents/PDF local archive | 6,8 | PDF saved, all piece PINs |
| 10 | Batch processing/history/downloads | 5,8,9 | Partial failures safe, restart works |
| 11 | Automated acceptance/safety tests | 4,7,8,9,10 | Full gate suite |
| 12 | Development + supervised Production verification | 11 | Explicit authorized signoff |
| 13 | Rates/availability/void enhancement (P1) | 12 | No regression |

## Engineering guardrails

- Unit tests mock SOAP calls; CI has **no** Purolator credentials.
- Validate imported XLSX shape/content size; don't execute macros/formulas or treat filenames as filesystem paths.
- Address corrections, manual reconciliation, environment switch and label regeneration need audit events.
- Reconciliation state is an operational workflow, not generic "retry" button.
- No API credentials, actual names/addresses, production key screenshots, Purolator confidential PDFs or shipment labels in Git.
- Do not auto-create/void production shipments as part of onboarding.
- Label success requires valid PDF bytes on disk. Submission success alone is not label success.

## Operational runbook (draft)

1. Create daily export; check workbook dates/headers and import summary.
2. Filter ready orders; correct/confirm Ship To, especially code/line inconsistencies.
3. Physically pack/count cartons and weigh; confirm dimensions.
4. Confirm sender, Purolator service, payer/account and Development vs Production.
5. Submit reviewed orders once; print saved labels, confirm Ship To/barcode/PIN.
6. For `needs_reconciliation`, stop and inspect carrier transactions by reference/PIN, then resolve explicitly without creating a second shipment.
7. Export daily PIN list, store local backup; review void/manifest requirements.

## Decision log

- Streamlit kept for earliest delivery; more complex frontends deferred.
- SOAP chosen to leverage supplied WSDLs and active account, REST migration postponed.
- Local SQLite chosen over session state; hosted app security out of initial scope.
- Real carrier testing gated; no production connectivity assumed from key status alone.

## Private WSDL local deployment (after approval)

The WSDL bundle was supplied outside GitHub and may contain proprietary documentation. Do **not** commit it. The SOAP client expects a private local layout:

```text
PUROLATOR_WSDL_DIR/
  Development/
    ShippingService.wsdl
    ShippingDocumentsService.wsdl
    EstimatingService.wsdl
    ServiceAvailabilityService.wsdl
  Production/
    (same four filenames)
```

WSDL endpoint hostnames are checked against `devwebservices.purolator.com` and `webservices.purolator.com`. The carrier adapter defaults to the local settings' Development environment and never invokes Production unless explicit operator approval is passed. Do not call the adapter from the UI until Task 8 has implemented the safe attempt journal and final review gate.
