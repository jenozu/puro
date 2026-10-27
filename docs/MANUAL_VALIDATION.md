# Task 12 — Controlled Purolator API and operational validation

**STOP: This procedure requires an authorized company-approved environment and explicit operator approval.** Tasks 1–11 use synthetic data and mock carrier services. Green CI does not establish production safety.

## Prerequisites (operator and IT)

- Confirm company IT approves running Python virtual environments, third-party libraries, and outbound Purolator HTTPS on the target workstation. Do **not** bypass blocked GitHub or company network restrictions.
- Use a private local copy of the repository distributed by an approved channel. Never put real customer exports, WSDLs, PINs, labels or credentials in the public GitHub repository.
- Confirm Purolator production account number and active credentials, warehouse sender name/address/phone, operational service, parcel size/weight, and preferred PDF printer/stock format.
- Place locally authorized WSDL/XSD service definitions in the `PUROLATOR_WSDL_DIR` layout described in `docs/BUILD_PLAN.md`. Avoid importing confidential carrier definitions into the public repo.
- Make a protected backup of the local SQLite file and plan retention for PDF labels and shipping records.

## Stage A — Offline application check (no carrier calls)

1. Start with `PUROLATOR_ENV=development`, `PURO_ENABLE_CARRIER=false`, `PURO_PRODUCTION_APPROVED=false`.
2. Upload a synthetic SAP `Orders` workbook whose Ship To Ottawa differs from Bill To Toronto; ensure the destination preview uses shipping fields from `RDR12` only.
3. Verify address validation blocks an empty Ship To street/postal code, invalid country, and an order containing distinct line Ship To codes.
4. Edit the address and make sure Ready is revoked until reviewed; reimport and restart to confirm the stored order is preserved.
5. Confirm the interface has **no enabled Create Shipment control** while the carrier flag is off.

## Stage B — Development environment test (authorized, no production charges)

1. Configure Development key/password/account and the approved sender information in **local** `.env`; keep Production authorization false.
2. Review Development WSDL endpoint host; it must be `devwebservices.purolator.com` over HTTPS. Check the real WSDL/header types in Zeep. Match service/version and operation request wrappers to the WSDL—do not assume mock-only tests prove this.
3. Enable `PURO_ENABLE_CARRIER=true` only after the above check and with authorized test data. Test carrier validation before creation.
4. Prepare a simple one-piece Domestic Ground shipment with explicit physical dimensions and actual allowed addresses per Purolator Development specifications.
5. Explicitly submit one **Development** shipment; confirm SOAP response structure, ShipmentPIN and piece PIN(s); request GetDocuments and verify the PDF barcodes/recipient/service/parcel count.
6. Test simulated timeout/unknown outcome using mocks, **not** by intentionally interrupting real Production transactions. Verify manual reconciliation before any retry.
7. Inspect label PDF print at your actual printer; verify paper size, alignment, barcode legibility and routing information.
8. Capture non-sensitive results and any carrier errors; redact all real data before reporting through public GitHub.

## Stage C — Supervised single-shipment Production trial

Only after Stage B passes, IT/company policy permits it, carrier account configuration is confirmed and the operator approves a **specific** potentially chargeable shipment:

1. Set `PUROLATOR_ENV=production` and `PURO_PRODUCTION_APPROVED=true` **locally** only for an authorized trial, with `PURO_ENABLE_CARRIER=true`. The UI must also require a deliberate final confirmation. Never commit these values.
2. Verify the recipient from order-specific SAP Ship To against the actual paper sales order and package; check service, payer, account, sender, parcel count and measured weights/dimensions.
3. Execute exactly one approved shipment, preserve ShipmentPIN/piece PINs, save PDF, print and visually inspect. Record actual outcome in a protected location.
4. Verify restart retains shipment record and that double-click/reimport/unknown outcome cannot trigger another CreateShipment.
5. Verify a safe operational void/reconciliation process with Purolator support and appropriate permissions.
6. Set `PURO_PRODUCTION_APPROVED=false` again after the trial unless operations formally authorize ongoing production use.

## Release gate

Task 12 may be marked **done** only with operator sign-off documenting: authorized API access, real Development integration and PDF output, actual approved Production shipment, confirmed Ship To, printed/scanned label, duplicate/reconciliation handling and acceptable security/storage policies. Without all checks, Puro is **not production-ready**.
