import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.channels.telegram import BotReply
from app.config import Settings
from app.core.llm.fake import FakeProvider
from app.main import create_app
from app.security import verify_telegram_init_data
from tests.conftest import _HASH
from tests.test_regular_telegram import make_handler

TOKEN = "123456:TEST-token"
URL = "https://atlas.tail1234.ts.net/"


def init_data(user_id=111, token=TOKEN, auth_date=None, **extra) -> str:
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Слава"}, ensure_ascii=False),
        **extra,
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_verify_init_data():
    assert verify_telegram_init_data(init_data(), TOKEN)["id"] == 111
    assert verify_telegram_init_data(init_data(signature="abc"), TOKEN)["id"] == 111
    with pytest.raises(ValueError, match="signature"):
        verify_telegram_init_data(init_data(token="999:other"), TOKEN)
    with pytest.raises(ValueError, match="signature"):
        verify_telegram_init_data(init_data().replace("111", "222"), TOKEN)
    with pytest.raises(ValueError, match="expired"):
        verify_telegram_init_data(init_data(auth_date=time.time() - 2 * 86400), TOKEN)
    with pytest.raises(ValueError):
        verify_telegram_init_data("garbage", TOKEN)


def test_miniapp_url_must_be_https():
    with pytest.raises(ValueError):
        Settings(_env_file=None, telegram_miniapp_url="http://atlas.local/")


@pytest.fixture
async def tg_client(tmp_path):
    st = Settings(
        _env_file=None,
        data_dir=tmp_path,
        password_hash=_HASH,
        llm_provider="fake",
        telegram_bot_token=TOKEN,
        telegram_allowed_chat_ids="111",
        telegram_api_base="http://127.0.0.1:9",  # bot polling fails fast, harmless
        telegram_miniapp_url=URL,
        scheduler_sweep_seconds=3600,
    )
    app = create_app(st, provider=FakeProvider())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            yield c


async def test_telegram_login(tg_client):
    c = tg_client
    assert (await c.post("/api/auth/telegram", json={"init_data": "x=1&hash=00"})).status_code == 401
    assert (await c.post("/api/auth/telegram", json={"init_data": init_data(user_id=222)})).status_code == 403
    assert (await c.get("/api/auth/me")).json()["authenticated"] is False

    r = await c.post("/api/auth/telegram", json={"init_data": init_data()})
    assert r.status_code == 200 and r.json()["csrf_token"]
    me = (await c.get("/api/auth/me")).json()
    assert me["authenticated"] is True
    # the session works for the normal API, with the CSRF token
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await c.get("/api/conversations")).status_code == 200


async def test_telegram_login_disabled_without_url(client):
    assert (await client.post("/api/auth/telegram", json={"init_data": init_data()})).status_code == 404


async def test_app_command(db, settings):
    h, *_ = make_handler(db, settings)
    assert "TELEGRAM_MINIAPP_URL" in await h(1, "/app")
    h.monitor.settings = settings.model_copy(
        update={"telegram_bot_token": SecretStr(TOKEN), "telegram_allowed_chat_ids": "1", "telegram_miniapp_url": URL}
    )
    reply = await h(1, "/app")
    assert isinstance(reply, BotReply)
    assert reply.markup() == {"inline_keyboard": [[{"text": "Открыть Атлас", "web_app": {"url": URL}}]]}
