from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.core.llm.fake import FakeProvider
from app.db.session import Database, migrate
from app.main import create_app
from app.security import hash_password

PASSWORD = "correct horse battery"
_HASH = hash_password(PASSWORD)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        password_hash=_HASH,
        llm_provider="fake",
        timezone="Europe/Moscow",
        scheduler_sweep_seconds=3600,
    )


@pytest.fixture
async def db(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await migrate(settings.database_url)
    database = Database(settings.database_url)
    yield database
    await database.dispose()


@pytest.fixture
def fake_provider(settings):
    return FakeProvider(tz=settings.tz)


@pytest.fixture
async def app(settings, fake_provider):
    application = create_app(settings, provider=fake_provider)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def authed(client):
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client
