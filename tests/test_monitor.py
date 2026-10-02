from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Lead
from app.monitor import checks
from app.monitor.alerts import AlertManager
from app.monitor.checks import ALARM, OK, WARN, CheckResult, evaluate_status
from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.monitor.site import HealthProbe, LeadItem, SiteError

NOW = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 MSK


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeMessenger:
    def __init__(self, fail_times=0):
        self.sent: list[str] = []
        self.fail_times = fail_times

    async def send(self, text, title="Атлас"):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("send failed")
        self.sent.append(text)

    async def send_lead(self, text):
        await self.send(text, title="Новая заявка")

    async def send_work(self, text, title="Forbsa"):
        await self.send(text, title=title)


# --------------------------------------------------------------- thresholds


def status(**overrides):
    base = {
        "generatedAt": NOW.timestamp() - 60,
        "diskPercent": 50,
        "memAvailableMb": 400,
        "swapUsedMb": 100,
        "dbBackupAgeHours": 5,
        "mediaBackupAgeHours": 5,
        "certDaysLeft": 60,
        "rebootNeeded": False,
        "webRunning": 1,
        "dbRunning": 1,
        "errorsLastHour": 0,
        "sshBanned": 3,
    }
    base.update(overrides)
    return base


def level(results, key):
    return next(r.level for r in results if r.key == key)


def test_all_ok():
    assert {r.level for r in evaluate_status(status(), NOW)} == {OK}


@pytest.mark.parametrize(
    "field, key, ok, warn, alarm",
    [
        ("diskPercent", "disk", 79, 80, 90),
        ("memAvailableMb", "memory", 120, 119, 59),
        ("swapUsedMb", "swap", 1499, 1500, 1900),
        ("dbBackupAgeHours", "backup_db", 30, 31, 51),
        ("mediaBackupAgeHours", "backup_media", 30, 31, 51),
        ("offsiteBackupAgeHours", "backup_offsite", 30, 31, 51),
        ("certDaysLeft", "cert", 21, 20, 6),
        ("errorsLastHour", "errors", 19, 20, 100),
    ],
)
def test_threshold_rows(field, key, ok, warn, alarm):
    assert level(evaluate_status(status(**{field: ok}), NOW), key) == OK
    assert level(evaluate_status(status(**{field: warn}), NOW), key) == WARN
    assert level(evaluate_status(status(**{field: alarm}), NOW), key) == ALARM


def test_missing_backup_and_cert_are_alarms():
    r = evaluate_status(status(dbBackupAgeHours=-1, certDaysLeft=-1), NOW)
    assert level(r, "backup_db") == ALARM and level(r, "cert") == ALARM


def test_status_freshness():
    assert level(evaluate_status(status(generatedAt=NOW.timestamp() - 14 * 60), NOW), "status_fresh") == OK
    assert level(evaluate_status(status(generatedAt=NOW.timestamp() - 16 * 60), NOW), "status_fresh") == WARN
    assert level(evaluate_status(status(generatedAt=NOW.timestamp() - 31 * 60), NOW), "status_fresh") == ALARM


def test_containers_and_reboot():
    assert level(evaluate_status(status(webRunning=0), NOW), "containers") == ALARM
    assert level(evaluate_status(status(rebootNeeded=True), NOW, NOW - timedelta(days=13)), "reboot") == OK
    assert level(evaluate_status(status(rebootNeeded=True), NOW, NOW - timedelta(days=15)), "reboot") == WARN


def test_offsite_is_optional():
    assert not any(r.key == "backup_offsite" for r in evaluate_status(status(), NOW))


def test_health_and_feed_results():
    assert checks.health_result(0, "", 0.3).level == OK
    assert checks.health_result(0, "", 2.5).level == WARN
    assert checks.health_result(2, "HTTP 502", 0.1).level == OK
    assert checks.health_result(3, "HTTP 502", 0.1).level == ALARM
    assert checks.feed_result(3, "HTTP 401").level == ALARM
    assert checks.stub_result('<meta name="robots" content="noindex">').level == ALARM
    assert checks.stub_result("<h1>Forbsa</h1>").level == OK


# ------------------------------------------------------------------ alerts


async def test_alert_noise_rules(db):
    clock, m = Clock(), FakeMessenger()
    am = AlertManager(db, m, clock=clock)
    warn = CheckResult("disk", "Диск", WARN, "83%")

    assert len(await am.process([warn])) == 1
    clock.advance(hours=5)
    assert await am.process([warn]) == []  # repeat suppressed (< 6 h)
    clock.advance(hours=1, minutes=1)
    assert (await am.process([warn]))[0].startswith("Всё ещё предупреждение")

    clock.advance(minutes=5)
    alarm = CheckResult("disk", "Диск", ALARM, "91%")
    assert (await am.process([alarm]))[0].startswith("Тревога")  # escalation is immediate
    clock.advance(minutes=30)
    assert await am.process([alarm]) == []
    clock.advance(minutes=31)
    assert len(await am.process([alarm])) == 1  # alarm repeats hourly

    clock.advance(minutes=10)
    sent = await am.process([CheckResult("disk", "Диск", OK)])
    assert sent[0].startswith("Восстановлено: Диск") and "длилась" in sent[0]
    assert await am.process([CheckResult("disk", "Диск", OK)]) == []  # only once


async def test_mute_silences_warnings_only(db):
    clock, m = Clock(), FakeMessenger()
    am = AlertManager(db, m, clock=clock)
    await am.set_mute(NOW + timedelta(hours=2))
    assert await am.process([CheckResult("disk", "Диск", WARN)]) == []
    assert len(await am.process([CheckResult("health", "Сайт не отвечает", ALARM)])) == 1
    # a warning that was never sent recovers silently
    assert await am.process([CheckResult("disk", "Диск", OK)]) == []


async def test_explanation_attached_to_first_message(db):
    async def explain(problem, results):
        return "Что проверить: место на диске."

    m = FakeMessenger()
    am = AlertManager(db, m, explainer=explain, clock=Clock())
    sent = await am.process([CheckResult("disk", "Диск", WARN, "83%")])
    assert "Что проверить" in sent[0]


# ------------------------------------------------------------------- leads


class FakeSite:
    def __init__(self):
        self.leads_data: list[dict] = []
        self.fail = False
        self.health_probe = HealthProbe(True, 0.2)

    async def leads(self, after):
        if self.fail:
            raise SiteError("HTTP 500")
        items = [x for x in self.leads_data if x["id"] > after][:50]
        latest = max([x["id"] for x in self.leads_data], default=0)
        # The real client drops everything except id/createdAt/type/source.
        return latest, [LeadItem(x["id"], x["createdAt"], x["type"], x["source"]) for x in items]

    async def health(self):
        return self.health_probe

    async def status(self):
        raise SiteError("not used")

    async def homepage(self):
        return ""


def make_service(db, settings, site, messenger, clock=None):
    clock = clock or Clock()
    am = AlertManager(db, messenger, clock=clock)
    return MonitorService(db, settings, messenger, am, RegularTasks(db, settings, clock=clock), site, clock=clock)


def lead(i, t="dealer", src="form"):
    return {"id": i, "createdAt": "2026-10-05T07:00:00Z", "type": t, "source": src}


async def test_leads_first_run_then_new_ones_once(db, settings):
    site, m = FakeSite(), FakeMessenger()
    site.leads_data = [lead(1), lead(2)]
    svc = make_service(db, settings, site, m)
    assert await svc.poll_leads() == 0  # history is not replayed
    site.leads_data.append(lead(3, "architect", "chat"))
    assert await svc.poll_leads() == 1
    assert "№3" in m.sent[0] and "архитектор" in m.sent[0] and "чат" in m.sent[0]
    assert await svc.poll_leads() == 0  # no duplicates

    # "restart": a fresh service on the same DB misses nothing and repeats nothing
    site.leads_data.append(lead(4))
    svc2 = make_service(db, settings, site, m)
    assert await svc2.poll_leads() == 1
    assert sum("№4" in t for t in m.sent) == 1


async def test_lead_send_failure_is_retried(db, settings):
    site, m = FakeSite(), FakeMessenger()
    svc = make_service(db, settings, site, m)
    await svc.poll_leads()
    site.leads_data.append(lead(10))
    m.fail_times = 1
    with pytest.raises(RuntimeError):
        await svc.poll_leads()
    assert await svc.poll_leads() == 1
    async with db.session() as s:
        assert (await s.get(Lead, 10)).notified is True


async def test_real_client_strips_personal_data():
    import httpx

    from app.config import Settings
    from app.monitor.site import SiteClient

    def handler(request):
        assert request.headers["x-feed-key"] == "k"
        return httpx.Response(
            200,
            json={"latestId": 7, "items": [{"id": 7, "createdAt": "x", "type": "dealer", "source": "form", "name": "Иван", "phone": "+7999"}]},
        )

    client = SiteClient(
        Settings(_env_file=None, site_base_url="https://site.test", site_feed_key="k"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    latest, items = await client.leads(0)
    assert latest == 7 and "Иван" not in repr(items) and "+7999" not in repr(items)


async def test_feed_failures_alarm_after_three(db, settings):
    site, m = FakeSite(), FakeMessenger()
    svc = make_service(db, settings, site, m)
    site.fail = True
    for _ in range(2):
        await svc.poll_leads()
    assert m.sent == []
    await svc.poll_leads()
    assert m.sent and m.sent[0].startswith("Тревога: Ошибка опроса заявок")


async def test_health_alarm_and_recovery(db, settings):
    site, m = FakeSite(), FakeMessenger()
    clock = Clock()
    svc = make_service(db, settings, site, m, clock)
    site.health_probe = HealthProbe(False, 0.1, "HTTP 502")
    for _ in range(3):
        await svc.poll_health()
        clock.advance(minutes=1)
    assert m.sent[-1].startswith("Тревога: Сайт не отвечает")
    site.health_probe = HealthProbe(True, 0.2)
    await svc.poll_health()
    assert m.sent[-1].startswith("Восстановлено")
    assert 0 < await svc.availability() < 100


# -------------------------------------------------------------- reports


async def test_reports(db, settings):
    site, m = FakeSite(), FakeMessenger()
    svc = make_service(db, settings, site, m)
    await svc.poll_leads()
    site.leads_data.append(lead(5))
    await svc.poll_leads()
    assert "№5" in await svc.leads_report()
    assert "Расход Claude API" in await svc.cost_report()
    summary = await svc.weekly_summary()
    assert "Сводка за неделю" in summary and "Заявок за неделю: 1" in summary


# ------------------------------------------------------------- leads bot


class _Tg:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text):
        from app.channels.telegram import TelegramError

        if self.fail:
            raise TelegramError("sendMessage: 403 Forbidden")
        self.sent.append((chat_id, text))


async def test_leads_go_to_separate_bot_and_fall_back(db, settings):
    from pydantic import SecretStr

    from app.events import EventBus
    from app.monitor.messenger import Messenger

    st = settings.model_copy(
        update={
            "telegram_bot_token": SecretStr("main"),
            "telegram_allowed_chat_ids": "1",
            "leads_telegram_bot_token": SecretStr("leads"),
            "leads_telegram_chat_ids": "2, 3",
        }
    )
    assert st.leads_bot_enabled and st.leads_chat_ids == {2, 3}
    main, leads = _Tg(), _Tg()
    m = Messenger(st, db, EventBus(), main, leads)
    await m.send_lead("Новая заявка №1")
    await m.send("Тревога: диск")
    assert sorted(leads.sent) == [(2, "Новая заявка №1"), (3, "Новая заявка №1")]
    assert main.sent == [(1, "Тревога: диск")]  # alerts stay in the main bot

    broken = Messenger(st, db, EventBus(), main, _Tg(fail=True))
    await broken.send_lead("Новая заявка №2")
    assert main.sent[-1] == (1, "Новая заявка №2")  # never lost


def test_explanation_is_plain_text():
    from app.monitor.explain import plain

    assert plain("**Что случилось:** health трижды вернул 502.\n\n* лишний пункт\n## Итог") == (
        "Что случилось: health трижды вернул 502.\nлишний пункт\nИтог"
    )
    assert plain("leads_feed: 502") == "leads_feed: 502"
