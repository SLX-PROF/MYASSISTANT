#!/usr/bin/env bash
# Atlas VPS hardening, mirrors section 1 of the Forbsa site checklist. Idempotent. Run: sudo bash ~/harden.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

echo "== 1.3 swap 2G"
if ! swapon --show | grep -q swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf && sysctl --system >/dev/null

echo "== 1.6 fail2ban"
apt-get install -y fail2ban >/dev/null
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "== 1.6.1 firewall (ssh + web open; everything else only via Tailscale)"
apt-get install -y ufw >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null          # must be before enable, or you lock yourself out
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow in on tailscale0 >/dev/null
ufw route allow in on tailscale0 >/dev/null   # keeps the Tailscale exit node working
ufw --force enable >/dev/null

echo "== 1.2 watchdog: restart atlas when stopped or unhealthy (systemd timer, cron is not installed here)"
cat > /usr/local/bin/atlas-watchdog.sh <<'EOF'
#!/bin/sh
s=$(docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' atlas 2>/dev/null)
case "$s" in
  "running healthy"|"running starting") ;;
  *) logger -t atlas-watchdog "atlas state '$s', restarting"; docker restart atlas ;;
esac
EOF
chmod +x /usr/local/bin/atlas-watchdog.sh
cat > /etc/systemd/system/atlas-watchdog.service <<'EOF'
[Unit]
Description=Restart Atlas if unhealthy
[Service]
Type=oneshot
ExecStart=/usr/local/bin/atlas-watchdog.sh
EOF
cat > /etc/systemd/system/atlas-watchdog.timer <<'EOF'
[Unit]
Description=Atlas watchdog every 2 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
EOF

echo "== 1.3.4 weekly docker cleanup"
cat > /etc/systemd/system/atlas-prune.service <<'EOF'
[Unit]
Description=Docker image and build cache cleanup
[Service]
Type=oneshot
ExecStart=/usr/bin/docker image prune -f
ExecStart=/usr/bin/docker builder prune -f --filter until=168h
EOF
cat > /etc/systemd/system/atlas-prune.timer <<'EOF'
[Unit]
Description=Weekly docker cleanup
[Timer]
OnCalendar=Sun 05:30
Persistent=true
[Install]
WantedBy=timers.target
EOF

echo "== 1.8 journald size cap"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/size.conf
systemctl restart systemd-journald

systemctl daemon-reload
systemctl enable --now atlas-watchdog.timer atlas-prune.timer >/dev/null

echo "== secrets: remove stray .env backup"
rm -f /home/atlas/atlas/.env.backup-2026-10-02

echo; echo "===== CHECK ====="
echo "-- sshd effective:"; sshd -T | grep -iE "^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication|pubkeyauthentication) "
echo "-- ufw:"; ufw status | head -12
echo "-- fail2ban:"; fail2ban-client status sshd | head -5
echo "-- swap:"; swapon --show
echo "-- timers:"; systemctl list-timers --no-pager | grep atlas
echo "-- atlas:"; docker inspect -f '{{.State.Health.Status}}' atlas
echo "-- reboot needed:"; ls /var/run/reboot-required 2>&1
