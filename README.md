# Jarvis — личный ИИ-ассистент

Этап 1: ядро, веб-чат, напоминания, задачи и память. Работает на вашем сервере. Веб-интерфейс — главный: его можно установить на телефон и компьютер как приложение (PWA).

- **Мозг сменный.** По умолчанию используется Claude API. Есть демо-режим без модели (`LLM_PROVIDER=fake`) и заготовка под другие модели.
- **Агент ничего не выполняет сам.** У него нет shell, файлов и интернета. Доступно только 11 инструментов: время, напоминания, задачи и память.
- **Приватность.** Секреты лежат только в `.env`. В логах по умолчанию нет текстов сообщений. Шрифты и иконки хранятся локально, запросов к CDN нет.

Скриншоты интерфейса лежат в [`docs/screenshots/`](docs/screenshots). Предложения и идеи на следующие этапы: [`docs/IDEAS_AND_CHANGES.md`](docs/IDEAS_AND_CHANGES.md). Установка на сервер описана в [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Быстрый старт через Docker (рекомендуется)

Нужен установленный Docker:
- **Mac / Windows:** [Docker Desktop](https://www.docker.com/products/docker-desktop/).
- **Ubuntu:** см. [`docs/DEPLOY.md`](docs/DEPLOY.md).

Команды ниже выполняются в терминале. На Windows используйте PowerShell. Каждая команда вводится по отдельности.

```bash
# 1. Скачать проект
git clone https://github.com/SLX-PROF/MYASSISTANT.git
cd MYASSISTANT

# 2. Создать файл настроек
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env

# 3. Собрать образ (первый раз ~2–3 минуты)
docker compose build

# 4. Придумать пароль и получить его хеш
docker compose run --rm jarvis python scripts/hash_password.py
```

Скрипт напечатает строку вида `PASSWORD_HASH='$argon2id$v=19$...'`. Дальше:
1. Откройте файл `.env` в любом текстовом редакторе.
2. Замените строку `PASSWORD_HASH=` на напечатанную, целиком и вместе с кавычками.
3. Впишите ключ Claude API в `ANTHROPIC_API_KEY=` (ключ создаётся на https://console.anthropic.com).
4. Сохраните файл.

```bash
# 5. Запустить
docker compose up -d

# 6. Открыть в браузере
#    http://127.0.0.1:8000
```

Полезные команды:

| Что сделать | Команда |
|---|---|
| Посмотреть логи | `docker compose logs -f` |
| Остановить | `docker compose down` |
| Перезапустить после правки `.env` | `docker compose up -d --force-recreate` |
| Обновить до новой версии | `git pull && docker compose up -d --build` |
| Резервная копия базы | `docker compose cp jarvis:/data/jarvis.db ./backup-jarvis.db` |

> Если ключа Claude пока нет, поставьте в `.env` `LLM_PROVIDER=fake`. Jarvis запустится в демо-режиме и будет понимать простые команды вроде «напомни через 2 минуты выпить воды» и «добавь задачу купить продукты на завтра».

---

## Запуск без Docker (для разработки)

Нужны **Python 3.12** и **Node.js 22**.

**macOS / Linux:**
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cd frontend && npm ci && npm run build && cd ..   # собирает интерфейс в app/web

cp .env.example .env
python scripts/hash_password.py                    # хеш вставить в .env
python -m app.main                                 # http://127.0.0.1:8000
```

**Windows (PowerShell):**
```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

cd frontend; npm ci; npm run build; cd ..

Copy-Item .env.example .env
python scripts/hash_password.py
python -m app.main
```

Режим разработки интерфейса с мгновенным обновлением: в одном терминале `python -m app.main`, в другом `cd frontend && npm run dev`. Затем откройте http://localhost:5173 (запросы к `/api` проксируются на бэкенд).

Зависимости устанавливаются через `pip` из `requirements.txt`, версии зафиксированы. Можно использовать и `uv`: `uv venv -p 3.12 && uv pip install -r requirements-dev.txt`.

---

## Тесты

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pytest -q
```

Тесты используют `FakeProvider` и не требуют ключа. Что проверяется:
- валидация аргументов инструментов;
- планировщик: срабатывание, повторы, восстановление после перезапуска, пропущенные напоминания;
- цикл агента: вызовы инструментов, лимит итераций, отказы модели, данные против инструкций;
- авторизация, CSRF и ограничение попыток входа;
- сквозные сценарии через API.

Проверка типов интерфейса: `cd frontend && npm run typecheck`.

---

## Настройки `.env`

| Переменная | Что это | По умолчанию |
|---|---|---|
| `PASSWORD_HASH` | хеш пароля (argon2), создаётся `scripts/hash_password.py` | — (обязательно) |
| `LLM_PROVIDER` | `anthropic` (Claude), `fake` (демо), `openai_compat` (заготовка) | `anthropic` |
| `ANTHROPIC_API_KEY` | ключ Claude API | — |
| `LLM_MODEL` | модель. Дешевле: `claude-sonnet-5-5` | `claude-opus-5-5` |
| `LLM_EFFORT` | глубина размышлений (и стоимость): `low` … `max` | `low` |
| `LLM_REFUSAL_FALLBACK` | если модель откажется, повторить на запасной модели | `true` |
| `TIMEZONE` | часовой пояс для «завтра в 10» | `Europe/Moscow` |
| `HOST` / `PORT` | адрес без Docker (наружу не открыт) | `127.0.0.1:8000` |
| `JARVIS_BIND` / `JARVIS_PORT` | адрес и порт в Docker | `127.0.0.1:8000` |
| `COOKIE_SECURE` | `true`, когда сайт работает по HTTPS | `false` |
| `SESSION_TTL_HOURS` | сколько живёт вход | `720` (30 дней) |
| `LOG_VERBOSE` | писать в лог тексты сообщений (только для отладки) | `false` |

---

## Как это устроено

```
app/
  main.py            запуск FastAPI, раздача собранного интерфейса
  config.py          настройки из .env
  security.py        пароль, сессии, CSRF, лимит входов, заголовки безопасности
  core/agent.py      цикл агента: модель → инструменты → модель … (макс. 8 шагов)
  core/prompts.py    системный промпт (русский), блок текущего времени
  core/llm/          сменный «мозг»: anthropic.py, fake.py, openai_compat.py (заготовка)
  tools/             реестр инструментов + 11 инструментов с валидацией Pydantic
  services/          логика напоминаний, задач, фактов, повторов, времени
  scheduler.py       планировщик напоминаний (APScheduler + БД как источник правды)
  channels/          доставка уведомлений: web.py сейчас; сюда же Telegram, ntfy, e-mail
  api/               REST + SSE: auth, chat, items, notifications
  db/                модели SQLAlchemy; миграции в alembic/
frontend/            Vite + React + TypeScript; собирается в app/web
tests/               pytest
```

**Потоковый чат.** `POST /api/conversations/{id}/messages` отдаёт события SSE: текст по кусочкам, «Создаю напоминание…», карточки. Агент работает в фоне: если закрыть вкладку посреди ответа, ответ всё равно будет дописан и сохранён.

**Напоминания.** Время срабатывания хранится в базе, поэтому переживает перезапуск. Напоминания, пропущенные за время простоя, приходят с пометкой «просрочено». Повторяющиеся (ежедневно, по дням недели, ежемесячно) сохраняют время на часах даже при переходе на летнее или зимнее время.

**Уведомления.** Приходят как сообщение в чате, всплывающее уведомление в браузере и значок непрочитанного. Если вкладка была закрыта, уведомление появится при следующем открытии.
