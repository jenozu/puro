#!/usr/bin/env bash
# Create the backup key pair (first run only), take one encrypted backup and prove it decrypts.
# The PRIVATE key is shown once on this terminal and never written to disk on the server.
# Needs: age, python3. No sudo. Run by deploy/install_vps.sh (or by hand).
set -euo pipefail
cd "$(dirname "$0")/.."
RECIP="${PURO_BACKUP_RECIPIENT_FILE:-$HOME/.config/puro/backup-recipient.txt}"
DEST="${PURO_BACKUP_DIR:-$HOME/puro-backups}"
KEYTXT=""

command -v age >/dev/null && command -v age-keygen >/dev/null || { echo "FAIL: age is not installed" >&2; exit 1; }

if [ ! -s "$RECIP" ]; then
  mkdir -p "$(dirname "$RECIP")"; chmod 700 "$(dirname "$RECIP")"
  KEYTXT="$(age-keygen 2>/dev/null)"
  PUB="$(printf '%s\n' "$KEYTXT" | sed -n 's/^# public key: //p')"
  [ -n "$PUB" ] || { echo "FAIL: could not read the generated public key" >&2; exit 1; }
  echo
  echo "=== BACKUP PRIVATE KEY: copy the next line into your password manager NOW ==="
  echo "=== It is NOT stored on this server. Without it the backups cannot be read.  ==="
  printf '%s\n' "$KEYTXT" | grep '^AGE-SECRET-KEY-'
  echo "=============================================================================="
  read -r -p "Type SAVED once it is stored somewhere safe (anything else aborts): " answer
  [ "$answer" = "SAVED" ] || { echo "FAIL: key not confirmed; no backup configured" >&2; exit 1; }
  printf '%s\n' "$PUB" > "$RECIP"; chmod 600 "$RECIP"
fi

PURO_BACKUP_RECIPIENT_FILE="$RECIP" PURO_BACKUP_DIR="$DEST" deploy/backup_puro.sh
NEWEST="$(ls -1t "$DEST"/puro-*.tar.age | head -1)"
if [ -n "$KEYTXT" ]; then
  LISTING="$(age -d -i <(printf '%s\n' "$KEYTXT") "$NEWEST" | tar -tf -)"
  printf '%s\n' "$LISTING" | grep -q '^puro-backup/data/' || { echo "FAIL: backup did not decrypt/list correctly" >&2; exit 1; }
  echo "OK:   backup decrypts with the new key and contains the data folder"
  unset KEYTXT
else
  echo "OK:   backup written with the existing key (restore-test it with your offline key at some point)"
fi
