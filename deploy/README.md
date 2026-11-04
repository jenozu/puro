# VPS deployment notes (Ubuntu, user `puro`, loopback only)

No secrets, WSDLs, exports or labels belong here. Streamlit stays bound to `127.0.0.1`;
reach it with `ssh -L 8501:127.0.0.1:8501 puro@<vps>`.

## 1. Preflight (read-only, offline, prints no secrets)

```bash
cd /home/puro/puro && git pull -q && bash scripts/vps_preflight.sh
```
Runs the tests, the Stage A checks (including the automated UI checks) and the WSDL wrapper
verification against your private Development WSDLs. Sends nothing to Purolator.

## 2. Service + encrypted backups (one paste, asks for sudo once)

```bash
bash deploy/install_vps.sh
```
Refuses unless the config is `development`, carrier off, production not approved. It installs `age`,
creates the backup key pair on first run (private key shown ONCE on your terminal, never stored on
the server: save it in your password manager and type SAVED), takes a backup and proves it decrypts,
installs `puro.service` and the daily `puro-backup.timer`, then verifies: healthy, survives a restart,
listens on `127.0.0.1:8501` only, enabled at boot, backup unit works. Also copy `~/puro-backups`
off the server now and then; the files are encrypted.

## 3. Stage B: one supervised Development shipment

Recipient = a Purolator Developer Test Aid address. The carrier flag is turned on for ONE command only
(`.env` stays `false`). A throwaway database `~/puro-stageB-data` is used, never `~/puro-data`.

```bash
# 3a. Validate only: calls ValidateShipment, creates nothing
PURO_ENABLE_CARRIER=true .venv/bin/python scripts/stage_b_dev_shipment.py --validate-only \
  --name "<name>" --street-no "<no>" --street "<street>" --city "<city>" --province ON \
  --postal "<postal>" --phone "<10 digits>"

# 3b. Exactly one Development shipment + label (same recipient arguments)
PURO_ENABLE_CARRIER=true .venv/bin/python scripts/stage_b_dev_shipment.py --ship --approve-development-shipment \
  --name "<name>" --street-no "<no>" --street "<street>" --city "<city>" --province ON \
  --postal "<postal>" --phone "<10 digits>"
```
Exit 4 = needs_reconciliation: do not retry, report the output. A second `--ship` is refused by the journal.
