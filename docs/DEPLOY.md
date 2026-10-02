# Установка на сервер (Ubuntu 24.04 + Docker)

Пошаговый план на день настройки сервера. Команды вводятся по одной. Если что-то пошло не так, пришлите мне вывод команды целиком.

## 0. Что понадобится

- **VPS за границей:** 1 vCPU, 1–2 ГБ RAM, 20 ГБ диска. Для этапа 1 этого с запасом. Если позже захотите локальное распознавание речи (Whisper), берите 4 ГБ RAM.
- **Домен или поддомен**, например `atlas.ваш-домен.ru`, с A-записью на IP сервера. Без домена можно обойтись, см. вариант Б.
- **Ключ Claude API**: https://console.anthropic.com → API Keys.

## 1. Базовая защита сервера

Подключитесь под root (`ssh root@IP`), затем выполните:

```bash
apt update && apt upgrade -y
adduser atlas                      # придумайте пароль
usermod -aG sudo atlas
# скопировать ваш SSH-ключ новому пользователю
rsync --archive --chown=atlas:atlas ~/.ssh /home/atlas
# файрвол: открыть только SSH и веб
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Проверьте в **новом** окне, что `ssh atlas@IP` работает. После этого отключите вход под root по паролю:

```bash
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
sudo apt install -y unattended-upgrades   # автоматические обновления безопасности
```

## 2. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker atlas
exit          # выйти и зайти снова, чтобы группа применилась
```

Проверка: `docker run --rm hello-world`.

## 3. Атлас

```bash
git clone https://github.com/SLX-PROF/MYASSISTANT.git atlas
cd atlas
cp .env.example .env
docker compose build
docker compose run --rm atlas python scripts/hash_password.py
nano .env        # вставить PASSWORD_HASH, ANTHROPIC_API_KEY; позже COOKIE_SECURE=true
docker compose up -d
docker compose logs -f     # выход — Ctrl+C
```

В логах должно появиться `Atlas started (llm=anthropic, ...)`. Приложение слушает только `127.0.0.1:8000`, из интернета его пока не видно. Так и задумано.

## 4. Вариант А: HTTPS через Caddy (нужен домен)

Caddy сам получает и продлевает сертификат Let's Encrypt.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Файл `/etc/caddy/Caddyfile` (`sudo nano /etc/caddy/Caddyfile`), всё содержимое заменить на:

```
atlas.ваш-домен.ru {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

В `.env` поставьте `COOKIE_SECURE=true` и выполните `docker compose up -d --force-recreate`. Откройте `https://atlas.ваш-домен.ru`. Установка как приложение (PWA) и уведомления работают **только по HTTPS**.

## 5. Вариант Б: без публичного доступа (Tailscale)

Это самый приватный вариант: Атлас виден только вашим устройствам, наружу не открыт ни один порт.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```

Установите Tailscale на телефон и ноутбук под тем же аккаунтом. Адрес Атласа будет вида `https://<имя-сервера>.<tailnet>.ts.net`. Порты 80/443 в ufw в этом варианте можно закрыть.

## 5а. Telegram, дежурство и второй фактор

1. Бот: в Telegram откройте @BotFather → `/newbot` → получите токен. Свой chat ID узнайте у @userinfobot.
2. В `.env` на сервере (`nano .env`) впишите `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `LLM_MONTHLY_BUDGET_USD=10`, а также параметры сайта: `SITE_BASE_URL`, `SITE_FEED_KEY`, `SITE_STATUS_PATH`, `ADMIN_URL`, `SITE_IP`. Токен и ключи никому не присылайте.
3. Второй фактор входа: `docker compose run --rm atlas python scripts/totp_setup.py`, ключ добавьте в приложение-аутентификатор, строку `TOTP_SECRET=...` — в `.env`.
4. `docker compose up -d --force-recreate`, затем напишите боту `/status`.

**Атлас внутри Telegram (Mini App).** Впишите в `.env` адрес из `tailscale serve`: `TELEGRAM_MINIAPP_URL=https://<имя>.<сеть>.ts.net/` и перезапустите. У бота появится кнопка «Атлас» рядом с полем ввода и команда `/app`. Вход без пароля: Telegram подтверждает ваш аккаунт, пускаются только ID из `TELEGRAM_ALLOWED_CHAT_IDS`. Работает в приложениях Telegram (iPhone, Android, Mac, Windows), когда на устройстве включён Tailscale. Тема «Системная» в настройках Атласа подстраивается под тему Telegram. Поставьте в самом Telegram код-пароль и двухэтапную аутентификацию: внутри Telegram вход идёт без пароля Атласа.

Заявки с сайта можно получать в **отдельном боте** (только уведомления, без команд): создайте ещё одного бота у @BotFather и впишите `LEADS_TELEGRAM_BOT_TOKEN` и `LEADS_TELEGRAM_CHAT_IDS` (можно несколько ID через запятую, например ваш и менеджера). Каждый получатель один раз открывает этого бота и нажимает Start. Если второй бот не настроен или недоступен, заявки приходят в основной.

В логах должно появиться `telegram=on, monitoring=on` (и `leads bot=on`, если включён второй бот). Регулярные задачи создаются сами; дату платежа за домен задайте в `DOMAIN_RENEWAL_DATE=ММ-ДД`, после этого появится и напоминание о домене.

## 6. Резервные копии

Фото-референсы контент-плана лежат в том же томе, в `/data/media/content`. Забрать их к себе: `docker compose cp atlas:/data/media ./media`.

Атлас сам делает копию базы каждую ночь в 04:15 (хранятся 14 последних, папка `/data/backups`). Сделать копию вручную:

```bash
docker compose exec atlas python scripts/backup_db.py
```

Забрать копии на свой компьютер: `scp -r atlas@IP:/var/lib/docker/volumes/atlas_atlas-data/_data/backups ./` (нужен sudo на сервере). Проще так: `docker compose cp atlas:/data/backups ./backups` на сервере, затем `scp`.

## 7. Обновление

```bash
cd ~/atlas && git pull && docker compose up -d --build
```

Миграции базы применяются автоматически при старте.

## 8. Если что-то не работает

| Симптом | Что проверить |
|---|---|
| «Демо-режим: не задан ANTHROPIC_API_KEY» | ключ в `.env`, затем `docker compose up -d --force-recreate` |
| «Пароль не настроен» | строка `PASSWORD_HASH='...'` в `.env`, с одинарными кавычками |
| После входа снова просит пароль | по HTTP стоит `COOKIE_SECURE=true`: либо включите HTTPS, либо поставьте `false` |
| Напоминание пришло с опозданием | часовой пояс `TIMEZONE`, логи `docker compose logs | grep reminder` |
| «Нет связи с сервером модели» | доступ сервера к `api.anthropic.com`: `curl -I https://api.anthropic.com` |
