#!/bin/sh
# Consistent copy of the vault database into ~/atlas/host/vault (14 kept).
# Atlas puts this folder into its weekly archive, which goes to your Telegram.
# The vault is encrypted with your master password, so the copy is useless without it.
set -eu
D="${VAULT_BACKUP_DIR:-$HOME/atlas/host/vault}"
mkdir -p "$D"
docker exec vaultwarden /vaultwarden backup >/dev/null
f=$(docker exec vaultwarden sh -c 'ls -1t /data/db_*.sqlite3 | head -1')
docker cp "vaultwarden:$f" "$D/db-$(date +%Y%m%d-%H%M%S).sqlite3"
docker exec vaultwarden sh -c 'rm -f /data/db_*.sqlite3'
ls -1t "$D"/db-*.sqlite3 | tail -n +15 | xargs -r rm
