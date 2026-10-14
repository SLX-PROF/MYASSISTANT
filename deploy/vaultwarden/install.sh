#!/usr/bin/env bash
# Run once with sudo on the server: sudo bash ~/atlas/deploy/vaultwarden/install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$HERE/backup.sh"
tailscale serve --bg --https=8443 http://127.0.0.1:8222
cat > /etc/systemd/system/vaultwarden-backup.service <<UNIT
[Unit]
Description=Vaultwarden database backup
[Service]
Type=oneshot
User=atlas
ExecStart=$HERE/backup.sh
UNIT
cat > /etc/systemd/system/vaultwarden-backup.timer <<'UNIT'
[Unit]
Description=Vaultwarden backup daily
[Timer]
OnCalendar=*-*-* 04:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now vaultwarden-backup.timer
echo "ok. First backup: sudo systemctl start vaultwarden-backup.service; ls ~atlas/atlas/host/vault"
tailscale serve status
