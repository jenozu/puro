# Puro — SAP-export-to-Purolator Shipping Workstation

> **Status: carrier integration and batch workflow implemented with mocked tests; not live-tested, certified, or authorized for production. Carrier actions are disabled by default. Task 12 (controlled real-world verification) remains.**

Puro is a proposed local-first Python/Streamlit workstation for shipping SAP Business One sales orders through Purolator E-Ship SOAP APIs **without live SAP/SQL access**. An authorized user exports SAP orders to XLSX/CSV, reviews the *order-level Ship To* addresses, confirms packages and billing, creates Purolator shipments, and saves printable labels and tracking PINs.

## What matters most

- **Ship To, not Bill To:** `RDR12` fields ending in `S` are authoritative for the order destination. Never silently replace a missing Ship To with the billing address, customer defaults or a guessed street.
- **Safe live shipping:** previews and validations before sending, explicit confirmation, durable transaction records, duplicate prevention, and a **Needs reconciliation** status for ambiguous API results.
- **No SAP connection:** XLSX/CSV import replaces SQL Server/ODBC. Import can be repeated without losing shipment history.
- **Local data:** SQLite stores order/shipment state; PDF labels are saved locally; secrets remain in environment variables.
- **Separate carrier adapter:** WSDL-driven Python SOAP client for Shipping, Get Documents, Estimates and Service Availability. Development and Production are isolated.

## Documentation

| Document | Purpose |
| --- | --- |
| [MVP scope](docs/MVP.md) | Definition of the first shippable release, exclusions and acceptance gates |
| [Product requirements](docs/PRD.md) | Personas, workflows, data contract, safety requirements, acceptance criteria |
| [Build plan](docs/BUILD_PLAN.md) | Sequenced implementation, testing and rollout |
| [SAP export contract](docs/SAP_EXPORT.md) | Workbook columns, source tables and Ship To rules |
| [Security guide](docs/SECURITY.md) | Secrets, public-repository considerations, retention |
| [Task Master guide](docs/TASKMASTER.md) | Install, configure, manage and verify project tasks |
| [Task Master PRD](.taskmaster/docs/prd.txt) | Machine-parseable PRD for Task Master |
| [Task backlog](.taskmaster/tasks/tasks.json) | Seeded, dependency-aware Task Master tasks |

## Current repository contents

The repository includes the Streamlit order review UI, SQLite shipment journal, Purolator SOAP adapter and PDF-label workflow, all exercised with fictional test responses. **No live Purolator API shipments have been tested**, and you must not regard this build as operational until Task 12's authorized Development and Production acceptance tests pass.

## Run locally in an IT-approved environment (Task 12 only for carrier verification)

Requirements: Windows or another supported Python environment, Python 3.11+, internet access to Purolator, an authorized Purolator E-Ship account and active Development/Production API credentials, and a properly structured SAP export.

```powershell
git clone https://github.com/jenozu/puro.git
cd puro
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Add secrets locally to .env (NEVER to GitHub)
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

The code includes `puro/carrier.py` (SOAP), `puro/payloads.py` (WSDL field mapping), `puro/journal.py`, `puro/shipping.py`, `puro/documents.py`, and `puro/batch.py`. Carrier integration is **disabled by default**. The application can import and review without touching Purolator.

### Configuration

Copy [`.env.example`](.env.example) to a local `.env`. Add **only your own credentials locally**; the screenshot showing active API keys does not expose or authorize their use in this repository. Do not commit screenshots of key screens, customer shipments, labels, original Purolator confidential PDFs, private WSDL bundles, or real SAP exports.

Suggested local settings: `PUROLATOR_ENV=development`, `PURO_ENABLE_CARRIER=false`, `PURO_PRODUCTION_APPROVED=false`, Purolator API key/password/account, warehouse name/street/phone and a private local `PUROLATOR_WSDL_DIR`. Do not set the carrier flags to true until approved operational verification; do not put real secret values into examples. The private WSDLs must be placed in the layout described in [BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Task Master AI integration

This repo includes an actual **Task Master-compatible task backlog**, PRD and Cursor MCP configuration. Task Master is a **developer workflow tool**, not a runtime shipping dependency. It does not require that Purolator secrets be shared with an AI model.

Task Master can be installed with Node.js/npm:

```powershell
npm install -g task-master-ai
task-master list
task-master next
task-master show 1
```

For AI-powered planning, configure a supported Task Master model/provider **on your machine** with `task-master models --setup` and its required credentials (or an authenticated supported CLI), then optionally run:

```powershell
task-master parse-prd .taskmaster/docs/prd.txt
```

**Warning:** `parse-prd` regenerates/replaces tasks unless intentionally managed. The repository already provides a seeded backlog; commit your work before re-parsing. Do **not** run `task-master init` over existing files without checking what it will overwrite. See [docs/TASKMASTER.md](docs/TASKMASTER.md).

## Proposed app architecture

```text
SAP-export.xlsx → import/validate → normalized orders → review & confirm
                                                        ↓
                                               transaction journal (SQLite)
                                                        ↓
                                        Purolator SOAP client (Development/Production)
                                                        ↓
                                  PINs + saved PDFs + history + reconciliation queue
```

Implemented modular packages include `puro/address.py`, `orders.py`, `store.py`, `journal.py`, `carrier.py`, `payloads.py`, `shipping.py`, `documents.py`, `batch.py` and `config.py`. Tests are in `tests/` with fictional data and mock responses.

## What works in the current increment

- Upload XLSX with an `Orders` sheet or flattened CSV.
- Download a blank XLSX template from the Import screen.
- Explicitly map SAP `RDR12.*S` shipping fields; no billing address fallback.
- Flag missing destination fields and conflicting line-level Ship To codes.
- Save new orders in SQLite and preserve them across browser restarts/reimports.
- Edit and review Ship To, package measurements and billing intent; mark an order Ready only after local checks.
- Export reviewed order details as CSV for verification.
- Run tests with `python -m pip install -r requirements-dev.txt` then `python -m pytest -q`.

**Implemented but unverified against Purolator live systems:** `ValidateShipment`, `CreateShipment`, `GetDocuments`, duplicate safeguards, package/payer payloads, label-PDF archive, and batch exports. Multi-piece data models are tested with synthetic data; actual shipment and piece handling requires real Development tests. **Not implemented:** real rate estimates, live tracking, manifests, voiding UI, printer-specific formatting and international/customs. `Ready` means locally reviewed, not shipped.

## Definition of ready for Production

1. Development-environment tests for valid/invalid addresses, charge types, multi-piece labels and failures.
2. Explicit Ship To vs Bill To mismatch test; never output Bill To as the destination.
3. Duplicate and unknown-response retry tests; store/restore state across restarts.
4. Save and visually verify actual label PDF/document bytes.
5. Confirm approved account, shipping origin and environment; authorize one carefully reviewed live test shipment.
6. Document reconciliation/void procedure and confirm shipping labels actually scan/print.

See [docs/MVP.md](docs/MVP.md) for full sign-off.

## Project provenance

Requirements were derived from a previously supplied Streamlit UI and README, SAP Business One screen captures/schema, and Purolator E-Ship SOAP documentation (Shipping 2.2.2; Estimates 2.2.2; Service Availability 2.0.4; Get Documents 1.4.0). Original `config.settings`, `data.sap_repository`, `services.purolator_client` and `utils.helpers` code was **not** provided. The current docs do not claim the old Purolator SOAP client has been verified.

**Privacy:** The repository is public. All examples use fictional data; private documentation and actual shipment records stay outside Git.
