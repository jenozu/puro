# Agent / AI developer instructions for Puro

Read `README.md`, `docs/MVP.md`, `docs/PRD.md`, `docs/BUILD_PLAN.md`, `docs/SAP_EXPORT.md`, and `docs/SECURITY.md` before implementing.

**Task Master is the task authority:** inspect `.taskmaster/tasks/tasks.json`, select the next ready task with the installed `task-master next` command when available, implement and test one task, update status using Task Master; see `docs/TASKMASTER.md`. Never silently regenerate the seeded tasks from PRD without reviewing changes.

## Non-negotiable rules

1. The destination is order-specific SAP `RDR12.*S` **Ship To**, never any `*B` Bill To field. Fail closed on missing/ambiguous shipping data.
2. Preserve every shipment/piece PIN and locally saved label. Do not mark a shipment label-ready before a valid PDF is written.
3. **Do not blindly retry `CreateShipment`** on timeout/unknown outcome; record `needs_reconciliation` and require external confirmation.
4. Default Purolator calls to Development; Production actions are explicit and never part of CI or automatic tests.
5. Do not commit credentials, real SAP exports, Purolator confidential guides or WSDL bundles, customer details or actual shipment labels. This repo is PUBLIC.
6. Prefer testable domain/services/modules instead of a monolithic Streamlit script. Separate UI, import, validation, SOAP client and SQLite.
7. Finish each task with tests and updated acceptance criteria, including synthetic Ship To != Bill To fixture and timeout/double-click simulations where applicable.
8. Only use official local Purolator WSDL specifications to determine SOAP request field types. Do not guess field names or service availability.

## SQL formatting convention

When the operator requests a **four-window SQL query**, provide exactly **one query** split into four code windows: Window 1 SELECT; Window 2 FROM/JOIN; Window 3 WHERE; Window 4 ORDER BY.

No real or production shipment API call should be executed without an explicit authorized operational confirmation.
