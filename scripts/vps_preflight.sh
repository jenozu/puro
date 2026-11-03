#!/usr/bin/env bash
# One-paste, READ-ONLY, OFFLINE VPS preflight. Prints no secrets, sends nothing to Purolator.
# Usage (from the repo root):  bash scripts/vps_preflight.sh
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
WSDL_DIR="${PUROLATOR_WSDL_DIR:-$HOME/puro-private/wsdl}"

echo "== repo =="; git log --oneline -1; git status -sb | head -1
echo "== exported in this shell (names only; these override .env) =="
env | cut -d= -f1 | grep -E '^(PURO|DEFAULT_SENDER_)' | sort | tr '\n' ' '; echo
echo "== config (from .env + shell) =="
$PY - <<'PY'
from puro.config import AppConfig
c = AppConfig.from_environment()
print("env:", c.environment, "| carrier:", c.carrier_enabled, "| prod_approved:", c.production_authorized)
print("key len:", len(c.api_key), "| pw len:", len(c.api_password), "| account ends: ..." + c.account_number[-3:])
print("missing:", c.missing_shipping_settings())
PY
stat -c '.env mode/owner: %a %U' .env
echo "== tests =="; $PY -m pytest -q 2>&1 | tail -2
echo "== stage A offline checks =="
$PY scripts/stage_a_check.py --out "$HOME/puro-stageA" | grep -E 'FAIL|ALL PASSED|FAILED'
echo "== WSDL wrapper verification (sends nothing) =="
$PY scripts/verify_wsdl_wrappers.py --wsdl-dir "$WSDL_DIR"
echo "== host =="
printf 'age: '; (command -v age >/dev/null && age --version) || echo "not installed"
printf 'listening 850x: '; ss -ltn 2>/dev/null | grep -E ':850[0-9]\b' | awk '{print $4}' | tr '\n' ' '; echo
printf 'puro.service: '; systemctl is-active puro 2>/dev/null || echo "not installed"
