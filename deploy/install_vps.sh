#!/usr/bin/env bash
# One-paste installer, run as user puro from /home/puro/puro:  bash deploy/install_vps.sh
# Installs age, creates/tests encrypted backups, installs the loopback-only systemd service
# and the daily backup timer, then verifies everything. Asks for your sudo password once.
# It REFUSES to install if carrier/production flags are not in the safe offline state.
# It never edits .env and never touches Purolator.
set -euo pipefail
cd "$(dirname "$0")/.."
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK:   $*"; }

[ "$(id -un)" = "puro" ] || fail "run as user puro"
[ "$PWD" = "/home/puro/puro" ] || fail "repo must be at /home/puro/puro (paths in deploy/puro.service)"
[ -x .venv/bin/streamlit ] || fail ".venv/bin/streamlit not found"

PATHS="$(.venv/bin/python - <<'PY'
from puro.config import AppConfig
c = AppConfig.from_environment()
bad = []
if c.environment != "development": bad.append("PUROLATOR_ENV is not development")
if c.carrier_enabled: bad.append("PURO_ENABLE_CARRIER is true")
if c.production_authorized: bad.append("PURO_PRODUCTION_APPROVED is true")
print("BAD:" + "; ".join(bad) if bad else "SAFE")
print(c.data_dir.expanduser().resolve())
print(c.labels_dir.expanduser().resolve())
PY
)"
[ "$(sed -n 1p <<<"$PATHS")" = "SAFE" ] || fail "$(sed -n 1p <<<"$PATHS") - the service is only installed in the safe offline state"
[ "$(sed -n 2p <<<"$PATHS")" = "/home/puro/puro-data" ] || fail "PURO_DATA_DIR resolves to $(sed -n 2p <<<"$PATHS"), but deploy/puro.service allows only /home/puro/puro-data"
[ "$(sed -n 3p <<<"$PATHS")" = "/home/puro/puro-labels" ] || fail "PURO_LABELS_DIR resolves to $(sed -n 3p <<<"$PATHS"), but deploy/puro.service allows only /home/puro/puro-labels"
[ -d /home/puro/puro-data ] && [ -d /home/puro/puro-labels ] || fail "data or labels folder missing"
ok "config is safe (development, carrier off, production not approved); data/labels paths match the unit"
if ss -ltn 2>/dev/null | grep -qE ':8501\b'; then fail "something already listens on 8501 (stop any manual streamlit first)"; fi

sudo -v
sudo apt-get update -qq
sudo apt-get install -y -qq age >/dev/null
ok "age installed: $(age --version)"

bash deploy/setup_backups.sh

sudo install -m 644 deploy/puro.service /etc/systemd/system/puro.service
sudo install -m 644 deploy/puro-backup.service /etc/systemd/system/puro-backup.service
sudo install -m 644 deploy/puro-backup.timer /etc/systemd/system/puro-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now puro.service puro-backup.timer

healthy() { for _ in $(seq 1 40); do [ "$(curl -fsS http://127.0.0.1:8501/_stcore/health 2>/dev/null)" = "ok" ] && return 0; sleep 1; done; return 1; }
healthy || { sudo journalctl -u puro -n 40 --no-pager >&2; fail "puro.service did not become healthy"; }
ok "puro.service healthy"
sudo systemctl restart puro.service
healthy || { sudo journalctl -u puro -n 40 --no-pager >&2; fail "puro.service unhealthy after restart"; }
ok "puro.service survives a restart"

LISTEN="$(ss -ltnH | awk '{print $4}' | grep -E ':8501$' | sort -u)"
[ "$LISTEN" = "127.0.0.1:8501" ] || fail "port 8501 is bound to: ${LISTEN:-nothing} (expected only 127.0.0.1:8501)"
ok "port 8501 listens on 127.0.0.1 only"
[ "$(systemctl is-enabled puro.service)" = "enabled" ] || fail "puro.service not enabled for boot"
[ "$(systemctl is-active puro-backup.timer)" = "active" ] || fail "backup timer not active"
ok "service enabled at boot; backup timer active"

BEFORE="$(ls -1 /home/puro/puro-backups/puro-*.tar.age | wc -l)"
sudo systemctl start puro-backup.service
AFTER="$(ls -1 /home/puro/puro-backups/puro-*.tar.age | wc -l)"
[ "$AFTER" -gt "$BEFORE" ] || { sudo journalctl -u puro-backup -n 20 --no-pager >&2; fail "backup unit ran but wrote no new backup"; }
ok "backup unit works under systemd ($AFTER backups)"
echo
echo "INSTALL: ALL OK  (reach the app with: ssh -L 8501:127.0.0.1:8501 puro@<vps>)"
echo "Reminder: also copy ~/puro-backups off the server now and then (they are encrypted)."
