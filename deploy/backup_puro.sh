#!/usr/bin/env bash
# Encrypted snapshot of Puro's SQLite journal + label PDFs, using `age` with a PUBLIC key.
# The VPS holds only the public key, so a compromised VPS cannot read old backups.
# Keep the matching private key OFF the VPS. Never reads .env; never prints data.
set -euo pipefail
umask 077

DATA_DIR="${PURO_DATA_DIR:-$HOME/puro-data}"
LABELS_DIR="${PURO_LABELS_DIR:-$HOME/puro-labels}"
DEST="${PURO_BACKUP_DIR:-$HOME/puro-backups}"
RECIPIENT_FILE="${PURO_BACKUP_RECIPIENT_FILE:-$HOME/.config/puro/backup-recipient.txt}"
KEEP="${PURO_BACKUP_KEEP:-60}"

[ -d "$DATA_DIR" ] || { echo "backup: data dir not found" >&2; exit 1; }
[ -s "$RECIPIENT_FILE" ] || { echo "backup: recipient (public key) file missing or empty" >&2; exit 1; }
grep -q '^age1' "$RECIPIENT_FILE" || { echo "backup: recipient file has no age public key" >&2; exit 1; }
if grep -q 'AGE-SECRET-KEY' "$RECIPIENT_FILE"; then
  echo "backup: recipient file contains a PRIVATE key; remove it from the VPS" >&2; exit 1
fi
command -v age >/dev/null || { echo "backup: 'age' is not installed" >&2; exit 1; }

mkdir -p "$DEST"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/puro-backup/data" "$STAGE/puro-backup/labels"

# Consistent copy of live SQLite files (online backup API); plain copy for everything else.
python3 - "$DATA_DIR" "$STAGE/puro-backup/data" <<'PY'
import shutil, sqlite3, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
for p in sorted(src.rglob("*")):
    rel = p.relative_to(src)
    if p.is_dir():
        (dst / rel).mkdir(parents=True, exist_ok=True)
    elif p.suffix in {".sqlite3", ".sqlite", ".db"}:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        a = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        b = sqlite3.connect(dst / rel)
        with b:
            a.backup(b)
        a.close(); b.close()
    elif p.name.endswith(("-wal", "-shm", "-journal")):
        continue  # folded into the online backup above
    else:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
PY
[ -d "$LABELS_DIR" ] && cp -a "$LABELS_DIR"/. "$STAGE/puro-backup/labels/" || true

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/puro-$STAMP.tar.age"
tar -C "$STAGE" -cf - puro-backup | age -R "$RECIPIENT_FILE" -o "$OUT.partial"
[ "$(head -c 21 "$OUT.partial")" = "age-encryption.org/v1" ] || { rm -f "$OUT.partial"; echo "backup: output is not an age file" >&2; exit 1; }
mv "$OUT.partial" "$OUT"

# Keep the newest $KEEP backups of this script's own output only.
ls -1t "$DEST"/puro-*.tar.age 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
echo "backup: wrote $(basename "$OUT") ($(stat -c %s "$OUT") bytes)"
