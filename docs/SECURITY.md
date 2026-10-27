# Security, Privacy and Carrier Safety

The GitHub repository **jenozu/puro is public**. Do not upload any API key, API password, customer export, production label/tracking batch, screenshot of secret values, original proprietary carrier PDF/WSDL bundle, or other confidential information.

## Credentials

- Store `PUROLATOR_API_KEY`, `PUROLATOR_API_PASSWORD`, `PUROLATOR_ACCOUNT_NUMBER` and sender details in a local `.env` outside version control; production credential access should be restricted to authorized staff.
- **Task Master AI provider keys are different from Purolator credentials.** Configure Task Master keys in the local environment or private MCP configuration; not in this repo.
- Default `PUROLATOR_ENV=development`; never promote to production based on a key merely displaying Active on the portal.
- Never log Basic Auth headers, SOAP credentials, customer addresses or entire raw shipping payloads. Redact and log request IDs, service types, hashes, statuses and carrier error codes instead.
- Use TLS certificate verification; no `verify=False`. Avoid setting up public tunnel/cloud Streamlit without proper access controls.

## Shipment safety

- Create an attempt journal record **before** calling `CreateShipment`.
- Carrier timeouts and connection resets after submitting a request are ambiguous: do **not** auto-retry; reconcile externally, using known reference/PIN where available.
- Make production create/void operations explicitly operator-initiated with clear confirmation.
- Ensure addresses are derived from Ship To fields and every saved label is verified.
- Package weights/dimensions are physical measurements, not automatically inferred as actual cartons from item master.

## Data storage

- SQLite and archived PDFs may contain customer personal information. Use least-privilege file permissions and controlled backups.
- Prefer local secure disk for MVP, not shared writable cloud storage.
- Support a configurable retention policy and data deletion/export rules when deploying; never silently delete documents needed for shipping evidence.
- Do not put original `puro-files.zip`, SAP schema exports, uploaded customer spreadsheets or label PDFs into the public repository.

## Testing and CI

- Unit tests use fictional fixtures and mocked SOAP responses only.
- CI must not have Production Purolator secrets or issue real shipments.
- Real Development calls require deliberate local testing. Real Production calls require explicit operator signoff.
- If any secret is accidentally committed, revoke/rotate it with Purolator/provider immediately; deleting the file alone does not remove Git history.

## Public documentation boundaries

This repository can explain expected SAP fields (`RDR12.*S`) and ordinary technical requirements, but should link to official Purolator documentation instead of mirroring confidential API guides. The application will load licensed local WSDL specifications at runtime or use authorized HTTPS endpoints according to deployment policy.
