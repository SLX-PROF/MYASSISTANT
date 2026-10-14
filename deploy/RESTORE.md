# Поднять сервер Атласа с нуля (Ubuntu, пользователь atlas)

Секреты здесь не хранятся: `.env` лежит в менеджере паролей, данные приходят из бэкапа.

1. Новый VPS, пользователь `atlas` с ключом SSH, пароль для sudo сохранить в менеджер паролей.
2. `sudo apt install -y docker.io docker-compose-v2 caddy` и `curl -fsSL https://tailscale.com/install.sh | sh`, затем `sudo tailscale up --advertise-exit-node`.
3. Если IP изменился: поправить `default_bind` в `deploy/Caddyfile`, затем `sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`. Обновить DNS-запись домена.
4. `git clone https://github.com/SLX-PROF/MYASSISTANT.git ~/atlas && cd ~/atlas`; `.env` из менеджера паролей положить в `~/atlas/.env`, права 644 (контейнер читает под uid 10001).
5. Данные: распаковать последний архив из Telegram (или `data/backups`) в том `atlas-data`: `atlas.db` и `media/`. Атлас при этом остановлен.
6. `docker compose up -d --build`, затем `sudo tailscale serve --bg 8000`.
7. `sudo bash deploy/harden.sh` (swap, файрвол, fail2ban, сторож, очистка Docker). Держать запасное окно SSH.
8. Проверка: `docker compose logs --tail 20 atlas` ждём «Atlas started»; `/status` в боте.
