"""Finance charts and goals, notes, voice, morning brief, server health, backups."""

import tarfile
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from app.briefing import morning_brief
from app.channels.telegram import TelegramBot, forwarded_text
from app.db.models import Task
from app.finance import service as fs
from app.monitor import host as hostmod
from app.monitor.checks import ALARM, OK, WARN
from app.notes import service as ns
from app.services import backup, kv
from app.services.voice import VoiceError

TODAY = date(2026, 10, 12)  # Monday


class Msgr:
    def __init__(self):
        self.sent = []

    async def send(self, text, title="Атлас"):
        self.sent.append(text)


@pytest.fixture
async def fdb(db):
    async with db.session() as s:
        await fs.ensure_defaults(s)
        await s.commit()
    return db


# ------------------------------------------------------------ finance


async def test_shares_history_goals(fdb):
    async with fdb.session() as s:
        cats = {c.name: c for c in await fs.categories(s)}
        await fs.add_transaction(s, amount=50000, kind="expense", day=date(2026, 10, 3), category=cats["Транспорт"], note="такси")
        await fs.add_transaction(s, amount=150000, kind="expense", day=date(2026, 10, 4), category=cats["Продукты"], note="еда")
        await fs.add_transaction(s, amount=9000000, kind="income", day=date(2026, 10, 5), category=cats["Зарплата"], note="зп")
        await fs.add_transaction(s, amount=20000, kind="expense", day=date(2026, 8, 5), category=None, note="?")
        g = await fs.add_goal(s, title="Отпуск", target=15000000, deadline=date(2027, 5, 31))
        sm = await fs.summary(s, "2026-10", TODAY)
        hist = await fs.history(s, "2026-10", 3)
        assert not fs.deposit(g, 5000000)
        out = fs.goal_out(g, TODAY)
        assert fs.deposit(g, 10000000) and g.done_at is not None
        fs.deposit(g, -100)
        assert g.done_at is None
    assert [r["name"] for r in sm["expense_shares"]] == ["Продукты", "Транспорт"]
    assert sm["income_shares"][0] == {"id": cats["Зарплата"].id, "name": "Зарплата", "color": cats["Зарплата"].color, "amount": 9000000}
    assert [h["month"] for h in hist] == ["2026-08", "2026-09", "2026-10"]
    assert hist[0]["expense"] == 20000 and hist[2] == {"month": "2026-10", "income": 9000000, "expense": 200000, "net": 8800000}
    # 100 000 left over Oct..May = 8 months
    assert out["pct"] == 33 and out["per_month"] == 1250000
    assert sm["goals"][0]["title"] == "Отпуск"


async def test_goals_api_and_history(authed):
    r = await authed.post("/api/finance/goals", json={"title": "Подушка", "target": 10000000, "deadline": "2027-03-31"})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    r = await authed.post(f"/api/finance/goals/{gid}/deposit", json={"amount": 10000000})
    assert r.json()["reached"] is True and r.json()["done"] is True
    r = await authed.patch(f"/api/finance/goals/{gid}", json={"target": 20000000})
    assert r.json()["done"] is False and r.json()["pct"] == 50
    assert len((await authed.get("/api/finance/goals")).json()) == 1
    assert len((await authed.get("/api/finance/history?months=6")).json()) == 6
    summary = (await authed.get("/api/finance/summary")).json()
    assert "expense_shares" in summary and "income_shares" in summary
    assert (await authed.delete(f"/api/finance/goals/{gid}")).json() == {"ok": True}


async def test_weekly_message(fdb):
    async with fdb.session() as s:
        assert await fs.weekly_message(s, TODAY) is None
        cats = {c.name: c for c in await fs.categories(s)}
        cats["Кафе и рестораны"].monthly_limit = 1000
        for day, amount in ((date(2026, 10, 6), 300000), (date(2026, 10, 11), 100000), (date(2026, 10, 1), 200000)):
            await fs.add_transaction(s, amount=amount, kind="expense", day=day, category=cats["Кафе и рестораны"], note="кафе")
        await fs.add_goal(s, title="Отпуск", target=100000, deadline=None)
        text = await fs.weekly_message(s, TODAY)
    assert "05.10–11.10" in text
    assert "Расходы: 4 000 ₽ (+100% к прошлой неделе)" in text
    assert "Перерасход в месяце: Кафе и рестораны (+5 000 ₽)" in text
    assert "Цель «Отпуск»: 0%" in text


# -------------------------------------------------------------- notes


async def test_notes_search_by_stems(db):
    async with db.session() as s:
        await ns.add_note(s, text="Идея: таргетированная реклама у блогеров", tags="маркетинг")
        await ns.add_note(s, text="Рецепт сырников", url="https://example.com/syrniki")
        with pytest.raises(ns.NoteError):
            await ns.add_note(s, text="  ")
        found = await ns.search(s, ["рекламу", "продвижение"])
        assert [n.text[:5] for n in found] == ["Идея:"]
        assert (await ns.search(s, ["сырники"]))[0].url == "https://example.com/syrniki"
        assert await ns.search(s, ["космос"]) == []
    assert ns.first_url("смотри https://a.ru/x?y=1, круто") == "https://a.ru/x?y=1"


async def test_notes_api(authed):
    r = await authed.post("/api/notes", json={"text": "купить гирю 16 кг", "tags": "#спорт"})
    assert r.status_code == 200 and r.json()["tags"] == ["спорт"]
    assert (await authed.get("/api/notes?q=гири")).json()[0]["text"] == "купить гирю 16 кг"
    assert len((await authed.get("/api/notes")).json()) == 1
    assert (await authed.delete(f"/api/notes/{r.json()['id']}")).json() == {"ok": True}


def test_forwarded_text():
    msg = {
        "text": "Статья про рекламу",
        "entities": [{"type": "text_link", "url": "https://blog.example/ads"}],
        "forward_origin": {"type": "channel", "chat": {"title": "Маркетинг", "username": "mkt"}, "message_id": 7},
    }
    assert forwarded_text(msg) == ("Статья про рекламу\n\nИсточник: Маркетинг", "https://blog.example/ads")
    msg.pop("entities")
    assert forwarded_text(msg)[1] == "https://t.me/mkt/7"


# --------------------------------------------------------- telegram bot


class Tg:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action="typing"):
        pass

    async def download_file(self, file_id, max_bytes=0):
        return b"OggS..."


class FakeTranscriber:
    def __init__(self, text="", fail=False):
        self.text, self.fail = text, fail

    async def transcribe(self, audio):
        if self.fail:
            raise VoiceError("Не получилось распознать голосовое.")
        return self.text


def _bot(db, settings, transcriber=None):
    st = settings.model_copy(update={"telegram_bot_token": SecretStr("x"), "telegram_allowed_chat_ids": "111", "content_telegram_chat_ids": "222"})
    tg, calls, notes = Tg(), [], []

    async def handler(chat, text):
        calls.append(text)
        return "ок"

    async def saver(text, url):
        notes.append((text, url))
        return "Сохранил"

    return TelegramBot(tg, st, db, handler, transcriber=transcriber, note_saver=saver), tg, calls, notes


async def test_voice_goes_to_handler(db, settings):
    bot, tg, calls, _ = _bot(db, settings, FakeTranscriber("кофе 350"))
    await bot.process_update({"update_id": 1, "message": {"chat": {"id": 111}, "voice": {"file_id": "f", "duration": 3}}})
    assert calls == ["кофе 350"] and tg.sent == [(111, "Распознал: «кофе 350»"), (111, "ок")]


async def test_voice_limits_and_errors(db, settings):
    bot, tg, calls, _ = _bot(db, settings, FakeTranscriber(fail=True))
    await bot.process_update({"update_id": 1, "message": {"chat": {"id": 111}, "voice": {"file_id": "f", "duration": 900}}})
    await bot.process_update({"update_id": 2, "message": {"chat": {"id": 111}, "voice": {"file_id": "f", "duration": 5}}})
    assert calls == [] and "длиннее 5 минут" in tg.sent[0][1] and "Не получилось" in tg.sent[1][1]
    # Voice from the content-only chat and strangers is not processed.
    bot, tg, calls, _ = _bot(db, settings, FakeTranscriber("секрет"))
    await bot.process_update({"update_id": 3, "message": {"chat": {"id": 999}, "voice": {"file_id": "f", "duration": 5}}})
    await bot.process_update({"update_id": 4, "message": {"chat": {"id": 222}, "voice": {"file_id": "f", "duration": 5}}})
    assert calls == [] and tg.sent == []
    bot, tg, calls, _ = _bot(db, settings, None)
    await bot.process_update({"update_id": 5, "message": {"chat": {"id": 111}, "voice": {"file_id": "f", "duration": 5}}})
    assert "выключены" in tg.sent[0][1]


async def test_forward_is_saved_as_note(db, settings):
    bot, tg, calls, notes = _bot(db, settings)
    await bot.process_update(
        {"update_id": 1, "message": {"chat": {"id": 111}, "text": "мысль", "forward_origin": {"type": "hidden_user", "sender_user_name": "Аня"}}}
    )
    assert notes == [("мысль\n\nИсточник: Аня", "")] and calls == [] and tg.sent == [(111, "Сохранил")]


# -------------------------------------------------------- morning brief


async def test_morning_brief(fdb, settings):
    tz = settings.tz
    now = datetime(2026, 10, 12, 8, 30, tzinfo=tz)
    async with fdb.session() as s:
        s.add(Task(title="Позвонить юристу", due_at=datetime(2026, 10, 12, 15, 0, tzinfo=tz), due_has_time=True))
        s.add(Task(title="Отчёт", due_at=datetime(2026, 10, 10, 0, 0, tzinfo=tz)))
        s.add(Task(title="Потом", due_at=datetime(2026, 10, 20, 0, 0, tzinfo=tz)))
        await s.commit()
    text = await morning_brief(fdb, settings, now, "+12°, ясно")
    assert text.startswith("Доброе утро! Понедельник, 12 октября")
    assert "Погода (Москва): +12°, ясно" in text
    assert "• Позвонить юристу — 15:00" in text and "Просрочено (1)" in text and "Потом" not in text
    assert "аявк" not in text  # work stays in the work bot


# ---------------------------------------------------- server and backups


def test_disk_and_memory_thresholds():
    gb = 1024**3
    assert hostmod.disk_result(40 * gb, 20 * gb, 10).level == OK
    assert hostmod.disk_result(40 * gb, 3 * gb, 10).level == WARN
    assert hostmod.disk_result(40 * gb, 1 * gb, 10).level == ALARM
    assert hostmod.memory_result(4_000_000, 200_000, 8).level == WARN
    assert hostmod.memory_result(4_000_000, 2_000_000, 8).level == OK


async def test_startup_notice_after_downtime(db, settings):
    m = Msgr()
    now = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    h = hostmod.HostMonitor(db, settings, m, alerts=None, clock=lambda: now)
    assert await h.startup_notice() is None  # first start ever
    async with db.session() as s:
        await kv.put(s, hostmod.ALIVE_KEY, (now - timedelta(minutes=2)).isoformat())
        await s.commit()
    assert await h.startup_notice() is None  # quick restart
    async with db.session() as s:
        await kv.put(s, hostmod.ALIVE_KEY, (now - timedelta(hours=3)).isoformat())
        await s.commit()
    assert "недоступен примерно 3 ч" in await h.startup_notice() and len(m.sent) == 1


async def test_weekly_archive(db, settings):
    media = settings.data_dir / "media" / "content"
    media.mkdir(parents=True)
    (media / "a.jpg").write_bytes(b"\xff\xd8photo")

    class Doc:
        def __init__(self):
            self.docs = []

        async def send_document(self, chat, name, data, caption=""):
            self.docs.append((chat, name, len(data), caption))

    tg = Doc()
    st = settings.model_copy(update={"telegram_bot_token": SecretStr("x"), "telegram_allowed_chat_ids": "111"})
    h = hostmod.HostMonitor(db, st, Msgr(), alerts=None, telegram=tg)
    path = await h.weekly_archive()
    with tarfile.open(path) as tar:
        assert set(tar.getnames()) >= {"atlas.db", "media/content/a.jpg"}
    assert tg.docs and tg.docs[0][0] == 111 and "база и фото" in tg.docs[0][3]
    for _ in range(3):
        backup.archive(settings.data_dir, keep=2)
    assert len(list((settings.data_dir / "backups" / "weekly").glob("*.tar.gz"))) == 2


NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 9000 10 0 0 0 0 0 0 9000 10 0 0 0 0 0 0
  ens3: {rx} 100 0 0 0 0 0 0 {tx} 100 0 0 0 0 0 0
docker0: 99999999999 1 0 0 0 0 0 0 99999999999 1 0 0 0 0 0 0
tailscale0: 5 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0
"""


async def test_traffic_counts_across_reboot_and_warns(db, settings, tmp_path):
    from app.monitor import traffic as tr

    gbb = 1024**3
    assert tr.pick_interface(tr.parse_net_dev(NET_DEV.format(rx=1, tx=2))) == "ens3"
    assert tr.period_start(date(2026, 10, 3), 5) == date(2026, 9, 5)
    assert tr.period_start(date(2026, 1, 3), 5) == date(2025, 12, 5)
    f = tmp_path / "net_dev"
    m = Msgr()
    clock = {"now": datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)}
    st = settings.model_copy(update={"traffic_limit_gb": 100})
    t = tr.TrafficMonitor(db, st, m, path=f, clock=lambda: clock["now"])
    assert await t.sample() is None and "crontab" in m.sent[0]  # counter file missing: one hint
    assert await t.sample() is None and len(m.sent) == 1
    f.write_text(NET_DEV.format(rx=10 * gbb, tx=50 * gbb))
    await t.sample()  # baseline
    f.write_text(NET_DEV.format(rx=20 * gbb, tx=130 * gbb))
    s1 = await t.sample()
    assert s1["tx"] == 80 * gbb and "80%" in m.sent[-1]
    f.write_text(NET_DEV.format(rx=gbb, tx=17 * gbb))  # reboot: counters restarted
    s2 = await t.sample()
    assert s2["tx"] == 97 * gbb and "95%" in m.sent[-1] and len(m.sent) == 3
    assert "Исходящий: 97.0 ГБ" in await t.report() and "97%" in await t.report()
    clock["now"] = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)  # new period
    f.write_text(NET_DEV.format(rx=gbb, tx=18 * gbb))
    s3 = await t.sample()
    assert s3["period"] == "2026-11-01" and s3["tx"] == 0
