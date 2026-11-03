# VPS deployment notes (Ubuntu, user `puro`, loopback only)

No secrets, WSDLs, exports or labels belong here. Streamlit stays bound to
`127.0.0.1`; reach it with `ssh -L 8501:127.0.0.1:8501 puro@<vps>`.

## Stage A helpers (offline, carrier off)

```bash
cd /home/puro/puro
.venv/bin/python scripts/stage_a_check.py --out ~/puro-stageA   # writes 2 fictional .xlsx, runs checks
# Throwaway data dir + separate port so real ~/puro-data is untouched:
PURO_DATA_DIR=$HOME/puro-stageA-data PURO_LABELS_DIR=$HOME/puro-stageA-labels \
PURO_ENABLE_CARRIER=false PURO_PRODUCTION_APPROVED=false PUROLATOR_ENV=development \
.venv/bin/streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true
```

## systemd service

```bash
sudo cp deploy/puro.service /etc/systemd/system/puro.service
sudo systemctl daemon-reload && sudo systemctl enable --now puro
systemctl status puro --no-pager; ss -ltn | grep 8501      # must show 127.0.0.1:8501 only
journalctl -u puro -n 50 --no-pager
```

## Encrypted backups (`age`, public key on the VPS, private key elsewhere)

```bash
sudo apt-get install -y age
# On your laptop (not the VPS):  age-keygen -o puro-backup-key.txt   -> prints "Public key: age1..."
mkdir -p ~/.config/puro && chmod 700 ~/.config/puro
echo 'age1...PUBLIC KEY ONLY...' > ~/.config/puro/backup-recipient.txt
deploy/backup_puro.sh && ls -l ~/puro-backups
sudo cp deploy/puro-backup.service deploy/puro-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now puro-backup.timer
systemctl list-timers puro-backup.timer --no-pager
# Restore test (laptop, after scp of one file):
age -d -i puro-backup-key.txt puro-*.tar.age | tar -tf -
```

Backups live on the same VPS, so also pull them off-box (they are encrypted):
`rsync -a puro@<vps>:puro-backups/ ~/puro-backups-offsite/`. The script keeps the newest
`PURO_BACKUP_KEEP` (default 60) snapshots and never touches the live data or labels.

## Stage B pre-flight (offline; sends nothing)

```bash
.venv/bin/python scripts/verify_wsdl_wrappers.py --wsdl-dir ~/puro-private/wsdl
```
