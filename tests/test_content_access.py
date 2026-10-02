from datetime import date, datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.channels.telegram import TelegramBot
from app.config import Settings
from app.content import service as cs
from app.core.llm.fake import FakeProvider
from app.events import EventBus
from app.main import create_app
from app.monitor.messenger import Messenger
from app.security import hash_password
from tests.conftest import _HASH, PASSWORD
from tests.test_miniapp import TOKEN, URL, init_data

HER_PASSWORD = "pastel content 2026"


@pytest.fixture
async def app_client(tmp_path):
    st = Settings(
        _env_file=None,
        data_dir=tmp_path,
        password_hash=_HASH,
        content_password_hash=hash_password(HER_PASSWORD),
        llm_provider="fake",
        telegram_bot_token=TOKEN,
        telegram_allowed_chat_ids="111",
        content_telegram_chat_ids="222",
        telegram_api_base="http://127.0.0.1:9",
        telegram_miniapp_url=URL,
        scheduler_sweep_seconds=3600,
    )
    app = create_app(st, provider=FakeProvider())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            yield c


async def test_content_password_sees_only_the_calendar(app_client):
    c = app_client
    r = await c.post("/api/auth/login", json={"password": HER_PASSWORD})
    assert r.status_code == 200 and r.json()["scope"] == "content" and "llm_provider" not in r.json()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await c.get("/api/auth/me")).json()["scope"] == "content"
    assert (await c.post("/api/content/items", json={"day": "2026-10-14", "title": "Nails day vlog"})).status_code == 200
    assert (await c.get("/api/content?start=2026-10-12&end=2026-10-18")).status_code == 200
    for path in ("/api/finance/summary", "/api/conversations", "/api/events", "/api/tasks", "/api/reminders", "/api/facts", "/api/settings/ui", "/api/notifications"):
        assert (await c.get(path)).status_code == 403, path
    assert (await c.post("/api/finance/parse", json={"text": "кофе 350"})).status_code == 403
    assert (await c.post("/api/auth/logout")).status_code == 200


async def test_owner_password_keeps_full_access(app_client):
    r = await app_client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.json()["scope"] == "all"
    assert (await app_client.get("/api/finance/summary")).status_code == 200


async def test_telegram_scopes(app_client):
    c = app_client
    r = await c.post("/api/auth/telegram", json={"init_data": init_data(user_id=222)})
    assert r.status_code == 200 and r.json()["scope"] == "content"
    assert (await c.get("/api/finance/summary")).status_code == 403
    assert (await c.post("/api/auth/telegram", json={"init_data": init_data(user_id=333)})).status_code == 403


def test_owner_id_never_downgraded():
    st = Settings(_env_file=None, telegram_bot_token="x", telegram_allowed_chat_ids="111", content_telegram_chat_ids="111,222")
    assert st.content_chat_ids == {222}


class _Tg:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


async def test_bot_gives_her_only_the_calendar_button(db, settings):
    st = settings.model_copy(
        update={"telegram_bot_token": SecretStr(TOKEN), "telegram_allowed_chat_ids": "111", "content_telegram_chat_ids": "222", "telegram_miniapp_url": URL}
    )
    calls = []

    async def handler(chat, text):
        calls.append(chat)
        return "ok"

    tg = _Tg()
    bot = TelegramBot(tg, st, db, handler)
    await bot.process_update({"update_id": 1, "message": {"chat": {"id": 222}, "text": "покажи мои финансы"}})
    assert calls == []  # never reaches the assistant
    chat, text, markup = tg.sent[0]
    assert chat == 222 and markup["inline_keyboard"][0][0]["web_app"]["url"] == URL.rstrip("/") + "/content"


async def test_content_notifications_go_to_her(db, settings):
    st = settings.model_copy(update={"telegram_bot_token": SecretStr(TOKEN), "telegram_allowed_chat_ids": "111", "content_telegram_chat_ids": "222"})
    tg = _Tg()
    m = Messenger(st, db, EventBus(), tg)
    await m.send_content("Сегодня по контент-плану: …")
    await m.send("Платёж через 2 дн.")
    assert [x[0] for x in tg.sent] == [222, 111]


async def test_publish_and_evening_reminders(db):
    tz = Settings(_env_file=None).tz
    async with db.session() as s:
        await cs.add_item(s, date(2026, 10, 14), title="Лучшие покупки", publish_time="19:00", stage="filmed")
        await cs.add_item(s, date(2026, 10, 14), title="Уже вышло", publish_time="19:00", stage="published")
        await cs.add_item(s, date(2026, 10, 15), title="Nails day vlog", publish_time="12:00")
        early = datetime(2026, 10, 14, 18, 0, tzinfo=tz)
        assert await cs.publish_soon(s, early, 30) == []
        soon = datetime(2026, 10, 14, 18, 35, tzinfo=tz)
        assert await cs.publish_soon(s, soon, 30) == ["Через 25 мин публикация: Лучшие покупки (19:00)."]
        assert await cs.publish_soon(s, soon, 30) == []  # once
        text = await cs.evening_message(s, date(2026, 10, 14))
        assert text == "Завтра по плану, ещё не снято:\n• Nails day vlog — публикация 12:00 (идея)"
        assert await cs.evening_message(s, date(2026, 10, 20)) is None


@pytest.fixture
async def public_client(tmp_path):
    st = Settings(
        _env_file=None,
        data_dir=tmp_path,
        password_hash=_HASH,
        content_password_hash=hash_password(HER_PASSWORD),
        totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        llm_provider="fake",
        telegram_bot_token=TOKEN,
        telegram_allowed_chat_ids="111",
        content_telegram_chat_ids="222",
        telegram_api_base="http://127.0.0.1:9",
        public_base_url="https://plan.example.dpdns.org",
        scheduler_sweep_seconds=3600,
    )
    assert st.content_miniapp_url == "https://plan.example.dpdns.org/content"
    app = create_app(st, provider=FakeProvider())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://plan.example.dpdns.org") as c:
            yield c


async def test_public_address_is_content_only(public_client):
    c = public_client
    assert (await c.get("/api/auth/me")).json()["totp_required"] is False
    # the owner's password does not work here at all
    assert (await c.post("/api/auth/login", json={"password": PASSWORD})).status_code == 401
    # non-content API is not even there
    for path in ("/api/finance/summary", "/api/conversations", "/api/health", "/api/events", "/api/settings/ui"):
        assert (await c.get(path)).status_code == 404, path
    # the owner's Telegram account also gets only the calendar here
    r = await c.post("/api/auth/telegram", json={"init_data": init_data(user_id=111)})
    assert r.status_code == 200 and r.json()["scope"] == "content"
    r = await c.post("/api/auth/login", json={"password": HER_PASSWORD})
    assert r.status_code == 200 and r.json()["scope"] == "content"
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await c.post("/api/content/items", json={"day": "2026-10-14", "title": "Nails"})).status_code == 200
    page = await c.get("/content")
    assert page.status_code in (200, 503)  # 503 when the UI is not built in this checkout


async def test_private_address_unaffected(public_client):
    async with AsyncClient(transport=public_client._transport, base_url="http://s1824923.tailnet.ts.net") as c:
        r = await c.post("/api/auth/login", json={"password": PASSWORD, "code": __import__("app.security", fromlist=["totp_code"]).totp_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")})
        assert r.status_code == 200 and r.json()["scope"] == "all"
        assert (await c.get("/api/finance/summary")).status_code == 200
