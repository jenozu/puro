#!/usr/bin/env bash
# One-time installer for the Puro autopilot's VPS side (see docs/AUTOPILOT.md).
#
#   curl -fsSLo /tmp/puro-ci-install.sh https://raw.githubusercontent.com/jenozu/puro/main/scripts/autopilot/install_vps_ci.sh
#   sudo bash /tmp/puro-ci-install.sh
#
# What it does (safe to re-run; re-running also refreshes the dispatcher from main):
#   * creates a separate, password-locked user `puro-ci` with its own clone + venv;
#     your live app in /home/puro/puro is never modified
#   * copies your Development .env for CI use and FORCES development / carrier off /
#     production not approved; copies ONLY the Development WSDL folder
#   * asks for the Purolator Developer Test Aid recipient (kept on this server only)
#   * installs /usr/local/bin/puro-ci (root-owned), generates a deploy key ON THIS SERVER and
#     pins it to that one program with `restrict,command=...` (no shell, no forwarding)
#   * runs a self-test and prints the 4 values to paste into GitHub repository secrets
# Re-running keeps the existing key; pass --new-key to rotate it (then update VPS_SSH_KEY).
set -euo pipefail

CI_USER=puro-ci
CI_HOME=/home/$CI_USER
SRC_REPO=/home/puro/puro
REPO_URL=https://github.com/jenozu/puro.git
KEY_COMMENT=puro-ci-github-actions
NEW_KEY=0; [ "${1:-}" = "--new-key" ] && NEW_KEY=1

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK:   $*"; }
warn() { echo "WARN: $*"; }
as_ci() { sudo -u "$CI_USER" -H "$@"; }

[ "$(id -u)" = 0 ] || fail "run with sudo: sudo bash $0"
[ -f "$SRC_REPO/.env" ] || fail "$SRC_REPO/.env not found (the Development settings to copy)"

# ---------------------------------------------------------------- packages
need=()
for pkg in git curl openssh-client python3-venv python3-pip; do dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg"); done
if [ ${#need[@]} -gt 0 ]; then
  apt-get update -qq && apt-get install -y -qq "${need[@]}" >/dev/null
fi
ok "packages present (git, curl, python3-venv)"

# ---------------------------------------------------------------- user + folders
if ! id "$CI_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "Puro CI (GitHub Actions, forced command only)" "$CI_USER"
fi
passwd -l "$CI_USER" >/dev/null 2>&1 || true
install -d -m 700 -o "$CI_USER" -g "$CI_USER" "$CI_HOME" "$CI_HOME/secure" "$CI_HOME/ci-work" "$CI_HOME/logs" "$CI_HOME/.ssh"
ok "user $CI_USER ready (password login locked)"

# ---------------------------------------------------------------- clone + venv
if [ -d "$CI_HOME/puro/.git" ]; then
  as_ci git -C "$CI_HOME/puro" fetch --quiet --prune origin
  as_ci git -C "$CI_HOME/puro" checkout --quiet --force --detach origin/main
else
  as_ci git clone --quiet "$REPO_URL" "$CI_HOME/puro"
fi
[ -x "$CI_HOME/puro/.venv/bin/python" ] || as_ci python3 -m venv "$CI_HOME/puro/.venv"
as_ci "$CI_HOME/puro/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$CI_HOME/puro/requirements-dev.txt"
ok "CI clone at $CI_HOME/puro ($(as_ci git -C "$CI_HOME/puro" log --oneline -1))"

# ---------------------------------------------------------------- safe .env copy
python3 - "$SRC_REPO/.env" "$CI_HOME/puro/.env" "$CI_HOME" <<'PY'
import os, sys
src, dst, home = sys.argv[1:]
forced = {
    "PUROLATOR_ENV": "development",
    "PURO_ENABLE_CARRIER": "false",
    "PURO_PRODUCTION_APPROVED": "false",
    "PUROLATOR_WSDL_DIR": f"{home}/secure/wsdl",
    "PURO_DATA_DIR": f"{home}/ci-work/data",
    "PURO_LABELS_DIR": f"{home}/ci-work/labels",
}
out, seen = [], set()
for line in open(src, encoding="utf-8"):
    key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
    if "=" in line and key in forced:
        out.append(f"{key}={forced[key]}\n"); seen.add(key)
    else:
        out.append(line if line.endswith("\n") else line + "\n")
out += [f"{k}={v}\n" for k, v in forced.items() if k not in seen]
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.writelines(["# CI copy made by install_vps_ci.sh. Development only; edits to the forced keys are refused.\n"] + out)
PY
chown "$CI_USER:$CI_USER" "$CI_HOME/puro/.env"; chmod 600 "$CI_HOME/puro/.env"
ok "CI .env written (development, carrier off, production NOT approved)"

# ---------------------------------------------------------------- Development WSDLs only
wsdl_src="$(sed -n 's/^[[:space:]]*PUROLATOR_WSDL_DIR[[:space:]]*=[[:space:]]*//p' "$SRC_REPO/.env" | tail -1 | tr -d "\"'")"
[ -n "$wsdl_src" ] || wsdl_src=/home/puro/puro-private/wsdl
# shellcheck disable=SC2088  # matches a literal "~/" written in .env
case "$wsdl_src" in
  "~/"*) wsdl_src="/home/puro/${wsdl_src#\~/}" ;;
  /*) ;;
  *) wsdl_src="$SRC_REPO/$wsdl_src" ;;
esac
rm -rf "$CI_HOME/secure/wsdl"; install -d -m 700 -o "$CI_USER" -g "$CI_USER" "$CI_HOME/secure/wsdl"
if [ -d "$wsdl_src/Development" ]; then
  cp -r "$wsdl_src/Development" "$CI_HOME/secure/wsdl/Development"
  chown -R "$CI_USER:$CI_USER" "$CI_HOME/secure/wsdl"; chmod -R go-rwx "$CI_HOME/secure/wsdl"
  ok "copied $(ls "$CI_HOME/secure/wsdl/Development" | wc -l) Development WSDL file(s); Production NOT copied"
else
  warn "no Development WSDL folder at $wsdl_src/Development; carrier actions will refuse until it exists. Re-run this installer after fixing."
fi

# ---------------------------------------------------------------- Test Aid recipient
TA="$CI_HOME/secure/testaid.env"
have_all=1
for k in NAME STREET_NO STREET CITY PROVINCE POSTAL PHONE; do grep -q "^$k=." "$TA" 2>/dev/null || have_all=0; done
ask=1
if [ "$have_all" = 1 ]; then
  read -r -p "Test Aid recipient already saved. Replace it? [y/N] " ans </dev/tty || ans=n
  [[ "$ans" =~ ^[Yy] ]] || ask=0
fi
if [ "$ask" = 1 ]; then
  if [ -r /dev/tty ]; then
    echo "Enter ONE recipient from Purolator's Developer Test Aid (Enter on NAME skips; you can re-run later)."
    read -r -p "  Name: " NAME </dev/tty || NAME=""
    if [ -n "$NAME" ]; then
      read -r -p "  Street number: " STREET_NO </dev/tty
      read -r -p "  Street name: " STREET </dev/tty
      read -r -p "  Unit (optional): " UNIT </dev/tty
      read -r -p "  City: " CITY </dev/tty
      read -r -p "  Province (2 letters): " PROVINCE </dev/tty
      read -r -p "  Postal code: " POSTAL </dev/tty
      read -r -p "  Phone (10 digits): " PHONE </dev/tty
      umask 077
      printf 'NAME=%s\nSTREET_NO=%s\nSTREET=%s\nUNIT=%s\nCITY=%s\nPROVINCE=%s\nPOSTAL=%s\nPHONE=%s\n' \
        "$NAME" "$STREET_NO" "$STREET" "$UNIT" "$CITY" "$PROVINCE" "$POSTAL" "$PHONE" > "$TA"
      chown "$CI_USER:$CI_USER" "$TA"; chmod 600 "$TA"
      ok "Test Aid recipient saved to $TA (this server only)"
    else
      warn "Test Aid skipped; stage-b actions will refuse until $TA is filled (re-run this installer)"
    fi
  else
    warn "no terminal for questions; fill $TA later by re-running this installer"
  fi
fi

# ---------------------------------------------------------------- dispatcher + key
install -o root -g root -m 755 "$CI_HOME/puro/scripts/autopilot/puro_ci.py" /usr/local/bin/puro-ci
AK="$CI_HOME/.ssh/authorized_keys"
KEYTMP=""
if [ "$NEW_KEY" = 1 ] || ! grep -q "$KEY_COMMENT" "$AK" 2>/dev/null; then
  KEYTMP="$(mktemp -d)"; chmod 700 "$KEYTMP"
  ssh-keygen -q -t ed25519 -N "" -C "$KEY_COMMENT" -f "$KEYTMP/key"
  printf 'restrict,command="/usr/local/bin/puro-ci" %s\n' "$(cat "$KEYTMP/key.pub")" > "$AK"
  ok "new deploy key generated on this server and pinned to /usr/local/bin/puro-ci"
else
  sed -i -E "s#^(restrict,command=\"[^\"]*\" )?(ssh-ed25519 [A-Za-z0-9+/=]+ $KEY_COMMENT)\$#restrict,command=\"/usr/local/bin/puro-ci\" \2#" "$AK"
  grep "$KEY_COMMENT" "$AK" | head -1 > "$AK.new" && mv "$AK.new" "$AK"  # only our one key may exist
  ok "existing deploy key kept (re-run with --new-key to rotate)"
fi
chown "$CI_USER:$CI_USER" "$AK"; chmod 600 "$AK"
ok "dispatcher installed at /usr/local/bin/puro-ci (root-owned)"

# ---------------------------------------------------------------- sshd sanity (read-only)
SSHD="$(sshd -T 2>/dev/null || true)"
PORT="$(awk '$1=="port"{print $2; exit}' <<<"$SSHD")"; PORT="${PORT:-22}"
allow="$(awk '$1=="allowusers"{$1=""; print}' <<<"$SSHD")"
if [ -n "$allow" ] && ! grep -qw "$CI_USER" <<<"$allow"; then
  warn "sshd has 'AllowUsers$allow' without $CI_USER: GitHub will be refused."
  warn "Fix: add $CI_USER to the AllowUsers line in /etc/ssh/sshd_config, then: sudo sshd -t && sudo systemctl reload ssh"
fi
grep -q '^pubkeyauthentication no' <<<"$SSHD" && warn "sshd has PubkeyAuthentication no; the deploy key cannot work"

# ---------------------------------------------------------------- self-test
echo "---- self-test: status ----"
as_ci env SSH_ORIGINAL_COMMAND=status /usr/local/bin/puro-ci || true
echo "---- self-test: refusal ----"
as_ci env SSH_ORIGINAL_COMMAND="bash -i" /usr/local/bin/puro-ci && fail "dispatcher did not refuse a shell" || ok "dispatcher refuses a shell"
echo "---- self-test: offline tests on main (about a minute) ----"
as_ci env SSH_ORIGINAL_COMMAND="test main" /usr/local/bin/puro-ci | tail -3 || warn "offline tests did not pass on main (Claude will look at this)"

# ---------------------------------------------------------------- lines for Claude
IP="$(curl -fsS -4 -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
HOSTKEY="$(awk '{print $1" "$2}' /etc/ssh/ssh_host_ed25519_key.pub)"
if [ "$PORT" = 22 ]; then KH="$IP $HOSTKEY"; else KH="[$IP]:$PORT $HOSTKEY"; fi
echo
echo "================ INSTALL DONE ================"
echo "Add these as repository secrets: github.com/jenozu/puro -> Settings -> Secrets and variables"
echo "-> Actions -> New repository secret. Name on the left, value = everything after the colon."
echo
echo "VPS_HOST:        $IP"
echo "VPS_PORT:        $PORT"
echo "VPS_KNOWN_HOSTS: $KH"
if [ -n "$KEYTMP" ]; then
  echo "VPS_SSH_KEY:     (all lines below, including the BEGIN and END lines)"
  cat "$KEYTMP/key"
  shred -u "$KEYTMP/key" 2>/dev/null || rm -f "$KEYTMP/key"
  rm -rf "$KEYTMP"
  echo
  echo "The private key above is not stored anywhere on this server. After saving it in GitHub,"
  echo "clear this terminal (run: clear && history -c). If you lose it, re-run with --new-key."
else
  echo "VPS_SSH_KEY:     unchanged (already in GitHub). Re-run with --new-key to rotate it."
fi
echo
echo "To cut GitHub's access at any time:  sudo truncate -s0 $AK"
