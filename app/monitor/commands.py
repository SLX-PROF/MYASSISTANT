"""Telegram commands. Reaches here only for allowed chat ids."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.channels.telegram import BotReply
from app.core.agent import Agent
from app.db.models import Conversation, utcnow
from app.db.session import Database
from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.services.chat import create_conversation
from app.services.timeparse import to_local_iso

log = logging.getLogger(__name__)

TELEGRAM_CONVERSATION = "Telegram"
CONFIRM_TTL = timedelta(minutes=2)
MAX_MUTE = timedelta(hours=24)

HELP = """Команды Атласа:
/status — состояние сайта и сервера
/leads — последние 5 заявок (без персональных данных)
/cost — расход Claude API за месяц
/mute 2h — заглушить предупреждения (тревоги не глушатся); /mute off — снять
/due — регулярные задачи на 14 дней и просроченные
/how <ключ> — подсказка по задаче
/done <ключ> — отметить задачу выполненной
/snooze <ключ> 3d — отложить (не больше 14 дней)
/lastdone — когда задачи выполнялись последний раз
/mail — сводка почты прямо сейчас
/app — открыть Атлас как приложение
/help — эта справка

Любой другой текст — вопрос Атласу: напоминания, задачи, финансы, заметки, вопросы о состоянии сайта.
Можно говорить голосом. Пересланное сообщение сохраняется в заметки."""


def parse_duration(text: str) -> timedelta | None:
    """'2h', '30m', '3d', '1d12h' -> timedelta."""
    parts = re.findall(r"(\d+)\s*([mhdмчд])", text.lower())
    if not parts or re.sub(r"[\d\smhdмчд]", "", text.lower()):
        return None
    total = timedelta()
    for n, unit in parts:
        n = int(n)
        total += {"m": timedelta(minutes=n), "м": timedelta(minutes=n), "h": timedelta(hours=n), "ч": timedelta(hours=n)}.get(
            unit, timedelta(days=n)
        )
    return total or None


class CommandHandler:
    def __init__(self, db: Database, monitor: MonitorService, regular: RegularTasks, agent: Agent, clock=utcnow, mail=None):
        self.mail = mail
        self.db = db
        self.monitor = monitor
        self.regular = regular
        self.agent = agent
        self.clock = clock
        self._pending: dict[int, tuple[str, datetime, datetime | None]] = {}  # chat -> (action, expires, arg)

    async def __call__(self, chat_id: int, text: str) -> str | BotReply | None:
        text = text.strip()
        if not text.startswith("/"):
            return await self._ask_agent(text)
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        handlers = {
            "/start": self._help,
            "/help": self._help,
            "/status": lambda c, a: self.monitor.status_report(),
            "/leads": lambda c, a: self.monitor.leads_report(),
            "/cost": lambda c, a: self.monitor.cost_report(),
            "/mute": self._mute,
            "/yes": self._confirm,
            "/due": lambda c, a: self.regular.due_report(),
            "/how": self._how,
            "/done": self._done,
            "/snooze": self._snooze,
            "/lastdone": lambda c, a: self.regular.lastdone_report(),
            "/app": self._app,
            "/mail": self._mail,
        }
        h = handlers.get(cmd)
        if h is None:
            return "Не знаю такой команды. /help — список команд."
        return await h(chat_id, arg)

    async def _help(self, chat_id: int, arg: str) -> str:
        return HELP

    async def _mail(self, chat_id: int, arg: str) -> str:
        if not self.mail:
            return "Почта не подключена: впишите MAIL_ACCOUNTS в .env."
        d = self.mail["digest"]
        letters, _, errors = await d.collect(self.clock())
        return await d.render(letters, errors) or "С прошлой сводки новых писем нет."

    async def _app(self, chat_id: int, arg: str) -> str | BotReply:
        url = self.monitor.settings.miniapp_url
        if not url:
            return "Приложение не настроено: впишите TELEGRAM_MINIAPP_URL в .env (адрес Атласа в Tailscale)."
        return BotReply("Атлас открывается внутри Telegram. Tailscale на устройстве должен быть включён.", ("Открыть Атлас", url))

    # ------------------------------------------------------------- mute

    async def _mute(self, chat_id: int, arg: str) -> str:
        now = self.clock()
        if arg.lower() in ("off", "0", "выкл", "снять"):
            self._pending[chat_id] = ("unmute", now + CONFIRM_TTL, None)
            return "Снять заглушение предупреждений? Подтвердите: /yes"
        dur = parse_duration(arg or "2h")
        if dur is None:
            return "Формат: /mute 2h (м, ч, д). Снять: /mute off"
        dur = min(dur, MAX_MUTE)
        until = now + dur
        self._pending[chat_id] = ("mute", now + CONFIRM_TTL, until)
        hhmm = to_local_iso(until, self.monitor.settings.tz)[11:16]
        return f"Заглушить предупреждения до {hhmm}? Тревоги всё равно будут приходить. Подтвердите: /yes"

    async def _confirm(self, chat_id: int, arg: str) -> str:
        pending = self._pending.pop(chat_id, None)
        if pending is None or pending[1] < self.clock():
            return "Нечего подтверждать."
        action, _, until = pending
        if action == "unmute":
            await self.monitor.alerts.set_mute(None)
            return "Заглушение снято."
        await self.monitor.alerts.set_mute(until)
        return f"Предупреждения заглушены до {to_local_iso(until, self.monitor.settings.tz)[11:16]}."

    # --------------------------------------------------- regular tasks

    async def _how(self, chat_id: int, arg: str) -> str:
        if not arg:
            return "Формат: /how <ключ>. Ключи: /due"
        t = await self.regular.get(arg)
        if t is None:
            return f"Нет задачи «{arg}». Список: /due"
        return f"{t.title}\n\n{t.runbook}\n\nГотово: /done {t.key}"

    async def _done(self, chat_id: int, arg: str) -> str:
        if not arg:
            return "Формат: /done <ключ>. Ключи: /due"
        return await self.regular.done(arg)

    async def _snooze(self, chat_id: int, arg: str) -> str:
        key, _, dur_txt = arg.partition(" ")
        dur = parse_duration(dur_txt or "1d")
        if not key or dur is None:
            return "Формат: /snooze <ключ> 3d"
        return await self.regular.snooze(key, dur)

    # ------------------------------------------------------------- agent

    async def _ask_agent(self, text: str) -> str:
        async with self.db.session() as s:
            conv = await s.scalar(select(Conversation).where(Conversation.title == TELEGRAM_CONVERSATION).limit(1))
            if conv is None:
                conv = await create_conversation(s, TELEGRAM_CONVERSATION)
                await s.commit()
            cid = conv.id
        texts: list[str] = []
        cards: list[str] = []
        error = None
        async for ev in self.agent.run(cid, text[:4000], purpose="telegram"):
            if ev["type"] == "assistant_message" and ev["message"]["text"]:
                texts.append(ev["message"]["text"])
            elif ev["type"] == "tool_result" and ev.get("card"):
                line = card_line(ev["card"], self.monitor.settings.tz)
                if line:
                    cards.append(line)
            elif ev["type"] == "error":
                error = ev["message"]
        self.monitor.messenger.bus.publish({"type": "conversation_updated", "conversation_id": cid})
        out = "\n\n".join(texts)
        if cards:
            out = (out + "\n\n" if out else "") + "\n".join(cards)
        if error:
            out = (out + "\n\n" if out else "") + error
        return out or "Готово."


def card_line(card: dict, tz) -> str | None:
    t = card.get("type")
    fmt = lambda iso: to_local_iso(datetime.fromisoformat(iso.replace("Z", "+00:00")), tz)[:16].replace("T", " ") if iso else "—"  # noqa: E731
    if t == "reminder":
        return f"[напоминание] {card['text']} — {fmt(card.get('next_fire_at'))} ({card.get('recurrence_label')})"
    if t == "task":
        due = fmt(card.get("due_at"))[:10] if card.get("due_at") else "без срока"
        return f"[задача] {card['title']} — {due}" + (" (выполнена)" if card.get("status") == "done" else "")
    if t in ("content", "finance", "note"):
        return f"[{ {'content': 'контент', 'finance': 'финансы', 'note': 'заметки'}[t] }] {card['text']}"
    if t == "fact":
        return f"[память] {card['text']}"
    if t == "reminder_list":
        return "\n".join(f"- {r['text']} — {fmt(r.get('next_fire_at'))}" for r in card["items"]) or "Напоминаний нет."
    if t == "task_list":
        return "\n".join(f"- #{x['id']} {x['title']}" for x in card["items"]) or "Задач нет."
    if t == "fact_list":
        return "\n".join(f"- {f['text']}" for f in card["items"]) or "Память пуста."
    return None
