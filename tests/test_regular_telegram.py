from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.channels.telegram import TelegramBot
from app.config import Settings
from app.core.agent import Agent
from app.core.llm.fake import FakeProvider, FakeTurn
from app.db.models import LLMUsage, RegularTask
from app.monitor.alerts import AlertManager
from app.monitor.commands import CommandHandler, parse_duration
from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.security import totp_code, verify_totp
from app.services import usage
from app.services.chat import create_conversation
from app.tools.builtin import build_registry
from tests.conftest import PASSWORD
from tests.test_monitor import Clock, FakeMessenger, FakeSite

NOW = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)  # 07:00 MSK


def monitored(settings: Settings) -> Settings:
    return settings.model_copy(update={"site_base_url": "https://forbsa.ru", "admin_url": "https://forbsa.ru/cp"})


# --------------------------------------------------------------- regular


async def test_seed_is_idempotent_and_domain_needs_date(db, settings):
    rt = RegularTasks(db, monitored(settings), clock=Clock(NOW))
    created, skipped = await rt.seed()
    assert len(created) == 10 and skipped == ["domain"]
    assert await rt.seed() == ([], ["domain"])

    with_domain = monitored(settings).model_copy(update={"domain_renewal_date": "12-15"})
    created, _ = await RegularTasks(db, with_domain, clock=Clock(NOW)).seed()
    assert created == ["domain"]
    async with db.session() as s:
        restore = await s.get(RegularTask, "restore")
        domain = await s.get(RegularTask, "domain")
        billing = await s.get(RegularTask, "billing")
    tz = settings.tz
    assert restore.next_due_at.astimezone(tz).isoformat()[:16] == "2027-01-01T10:00"
    assert domain.next_due_at.astimezone(tz).isoformat()[:16] == "2026-11-15T10:00"
    assert billing.next_due_at.astimezone(tz).isoformat()[:16] == "2026-10-05T10:00"  # Monday
    assert "/opt/forbsa-site" in billing.runbook


async def test_escalation_done_and_snooze(db, settings):
    clock = Clock(NOW)
    rt = RegularTasks(db, monitored(settings), clock=clock)
    await rt.seed()
    clock.t = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)  # Mon 10:00 MSK: billing + dialogs? (10:05 not yet)
    texts = await rt.check()
    billing = [t for t in texts if "Yandex Cloud" in t]
    assert billing and billing[0].startswith("Регулярная задача")
    # offsite was due on Oct 1 10:10 (not done yet) -> 3 days 23 h overdue
    assert any(t.startswith("Просрочено 3 дн.") and "вне сервера" in t for t in texts)
    # docker and reboot were done on Oct 1 per the spec -> not due yet
    assert not any("Docker" in t or "перезагрузка" in t for t in texts)
    assert await rt.check() == []  # each stage only once

    clock.advance(hours=24)
    assert any(t.startswith("Напоминаю") and "Yandex" in t for t in await rt.check())
    clock.advance(hours=48)
    assert any(t.startswith("Просрочено 3 дн.") for t in await rt.check())
    clock.advance(days=4)
    # billing's next weekly due passed too, but stage is per current due date
    assert any(t.startswith("Предупреждение") and "Yandex" in t for t in await rt.check())
    clock.advance(days=7)
    out = await rt.check()
    assert any(t.startswith("Тревога") and "Yandex" in t for t in out)  # high stakes
    assert not any(t.startswith("Тревога") and "диалоги" in t for t in out)  # normal task stops at warning

    reply = await rt.done("billing")
    assert reply.startswith("Отмечено") and "Следующий срок" in reply
    async with db.session() as s:
        b = await s.get(RegularTask, "billing")
    assert b.next_due_at > clock.t and b.stage == 0 and b.last_done_at == clock.t

    assert "14 дней" in await rt.snooze("dialogs", timedelta(days=20))
    clock.t = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
    assert (await rt.snooze("offsite", timedelta(days=2))).startswith("Отложено")
    assert "14 дней" in await rt.snooze("offsite", timedelta(days=4))  # 14-day cap counts from the original due
    assert "Нет задачи" in await rt.done("nope")
    assert "Регулярные задачи" in await rt.due_report()
    assert "billing" in await rt.lastdone_report()


# --------------------------------------------------------------- commands


def make_handler(db, settings, provider=None):
    clock = Clock(NOW)
    m = FakeMessenger()
    m.bus = type("Bus", (), {"publish": lambda self, ev: None})()
    st = monitored(settings)
    regular = RegularTasks(db, st, clock=clock)
    monitor = MonitorService(db, st, m, AlertManager(db, m, clock=clock), regular, FakeSite(), clock=clock)
    agent = Agent(db, st, provider or FakeProvider(script=[FakeTurn(text="Ок.")]), build_registry())
    return CommandHandler(db, monitor, regular, agent, clock=clock), monitor, regular


def test_parse_duration():
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("1d12h") == timedelta(days=1, hours=12)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("abc") is None


async def test_commands(db, settings):
    h, monitor, regular = make_handler(db, settings)
    await regular.seed()
    assert "/status" in await h(1, "/help")
    assert "Не знаю такой команды" in await h(1, "/rm -rf")
    assert "Подтвердите: /yes" in await h(1, "/mute 2h")
    assert (await h(1, "/yes")).startswith("Предупреждения заглушены")
    assert await monitor.alerts.mute_until() == NOW + timedelta(hours=2)
    assert await h(1, "/yes") == "Нечего подтверждать."
    assert "Yandex" in await h(1, "/how billing")
    assert (await h(1, "/done billing")).startswith("Отмечено")
    assert "Формат" in await h(1, "/snooze")
    assert "Регулярные задачи" in await h(1, "/due")
    assert "Расход" in await h(1, "/cost")
    assert "Заявок пока нет" in await h(1, "/leads")


async def test_free_text_goes_to_agent_with_cards(db, settings):
    provider = FakeProvider(
        script=[
            FakeTurn(tool_calls=[("create_task", {"title": "Позвонить", "due": "2030-01-02"})]),
            FakeTurn(text="Добавил."),
        ]
    )
    h, *_ = make_handler(db, settings, provider)
    reply = await h(1, "добавь задачу позвонить")
    assert "Добавил." in reply and "[задача] Позвонить" in reply


# ------------------------------------------------------------- telegram bot


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action="typing"):
        pass


async def test_bot_ignores_strangers(db, settings, caplog):
    st = settings.model_copy(update={"telegram_bot_token": "x", "telegram_allowed_chat_ids": "111"})
    calls = []

    async def handler(chat, text):
        calls.append((chat, text))
        return "ответ"

    tg = FakeTelegram()
    bot = TelegramBot(tg, st, db, handler)
    await bot.process_update({"update_id": 1, "message": {"chat": {"id": 999}, "text": "секретный текст"}})
    assert calls == [] and tg.sent == []
    assert "секретный текст" not in caplog.text
    await bot.process_update({"update_id": 2, "message": {"chat": {"id": 111}, "text": "/status"}})
    assert calls == [(111, "/status")] and tg.sent == [(111, "ответ")]


# ------------------------------------------------------------------ budget


async def test_budget_blocks_model_but_not_commands(db, settings):
    st = settings.model_copy(update={"llm_monthly_budget_usd": 1.0})
    async with db.session() as s:
        await usage.record(s, purpose="chat", model="claude-sonnet-5-5", usage={"output_tokens": 100_000})  # $1.00
        await s.commit()
        assert round(await usage.month_spend_usd(s), 2) == 1.0
    provider = FakeProvider(script=[FakeTurn(text="не должен ответить")])
    agent = Agent(db, st, provider, build_registry())
    async with db.session() as s:
        cid = (await create_conversation(s)).id
        await s.commit()
    events = [e async for e in agent.run(cid, "привет")]
    assert events[-1]["type"] == "error" and "лимит" in events[-1]["message"]
    assert provider.calls == []


async def test_usage_recorded_and_budget_watch(db, settings):
    st = monitored(settings).model_copy(update={"llm_monthly_budget_usd": 1.0})
    m = FakeMessenger()
    clock = Clock(datetime.now(timezone.utc))
    monitor = MonitorService(db, st, m, AlertManager(db, m, clock=clock), RegularTasks(db, st), None, clock=clock)
    provider = FakeProvider(script=[FakeTurn(text="Ок", usage={"input_tokens": 1000, "output_tokens": 75_000})])
    agent = Agent(db, st, provider, build_registry())
    agent.after_usage = monitor.budget_watch
    async with db.session() as s:
        cid = (await create_conversation(s)).id
        await s.commit()
    [e async for e in agent.run(cid, "привет")]
    async with db.session() as s:
        rows = (await s.scalars(select(LLMUsage))).all()
    assert rows[0].purpose == "chat" and rows[0].model == "fake-1"
    # fake model is priced as Opus-tier by default: 75k out * $20/M = $1.50 -> both 70% and 100%
    assert any("Тревога: месячный лимит" in t for t in m.sent)
    await monitor.budget_watch()
    assert sum("месячный лимит" in t for t in m.sent) == 1  # once per month


def test_price_table():
    assert usage.price_for("claude-sonnet-5-5")[0] == 2.0
    assert usage.price_for("claude-haiku-4-5")[0] == 1.0
    assert usage.cost_micro_usd("claude-sonnet-5-5", {"input_tokens": 1_000_000}) == 2_000_000


# --------------------------------------------------------------------- TOTP

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"


def test_totp_rfc6238_vector():
    assert totp_code(RFC_SECRET, 59) == "287082"
    assert totp_code(RFC_SECRET, 1111111109) == "081804"
    assert verify_totp(RFC_SECRET, "287082", for_time=59 + 30)  # previous step allowed
    assert not verify_totp(RFC_SECRET, "287082", for_time=59 + 120)
    assert not verify_totp(RFC_SECRET, "abc")


async def test_login_requires_second_factor(tmp_path):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from tests.conftest import _HASH

    st = Settings(_env_file=None, data_dir=tmp_path, password_hash=_HASH, llm_provider="fake", totp_secret=RFC_SECRET)
    app = create_app(st, provider=FakeProvider())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/auth/me")).json()["totp_required"] is True
            r = await c.post("/api/auth/login", json={"password": PASSWORD})
            assert r.status_code == 401 and "код" in r.json()["detail"]
            r = await c.post("/api/auth/login", json={"password": PASSWORD, "code": totp_code(RFC_SECRET)})
            assert r.status_code == 200


async def test_two_bots_split_personal_and_work(db, settings):
    from app.monitor.commands import WorkCommandHandler

    h, monitor, regular = make_handler(db, settings)
    personal = CommandHandler(db, monitor, regular, h.agent, include_work=False)
    # The personal bot knows nothing about the site.
    help_text = await personal(1, "/help")
    assert "/status" not in help_text and "/leads" not in help_text and "/mail" in help_text
    assert "Не знаю такой команды" in await personal(1, "/status")
    assert "Не знаю такой команды" in await personal(1, "/leads")
    # The work bot serves only site commands and does not talk to the assistant.
    work = WorkCommandHandler(monitor, regular)
    assert "/status" in await work(1, "/help")
    assert "Заявок пока нет" in await work(1, "/leads")
    assert "только команды по сайту" in await work(1, "напомни купить молоко")
    assert "только команды по сайту" in await work(1, "/mail")


async def test_site_traffic_goes_to_work_bot(db, settings):
    from pydantic import SecretStr

    from app.events import EventBus
    from app.monitor.messenger import Messenger

    class Tg:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    st = settings.model_copy(
        update={
            "telegram_bot_token": SecretStr("main"),
            "telegram_allowed_chat_ids": "1",
            "work_telegram_bot_token": SecretStr("work"),
            "work_telegram_chat_ids": "1",
        }
    )
    assert st.work_bot_enabled
    main, work = Tg(), Tg()
    m = Messenger(st, db, EventBus(), main, work)
    alerts = AlertManager(db, m, work=True)
    from app.monitor.checks import ALARM, CheckResult

    await alerts.process([CheckResult("health", "Сайт не отвечает", ALARM, "502")])
    await m.send_lead("Новая заявка №7")
    await m.send("Платёж завтра: аренда")
    assert [t for _, t in work.sent] == ["Тревога: Сайт не отвечает\n502", "Новая заявка №7"]
    assert [t for _, t in main.sent] == ["Платёж завтра: аренда"]
