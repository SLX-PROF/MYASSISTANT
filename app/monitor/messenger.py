"""Outgoing operational messages: Telegram when configured, otherwise the web chat.

Falling back to the web UI means alerts are never silently lost while
Telegram is not set up yet.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.channels.telegram import TelegramAPI, TelegramError
from app.config import Settings
from app.db.models import Conversation, Message, Notification
from app.db.session import Database
from app.events import EventBus
from app.services.chat import create_conversation, message_out, touch

log = logging.getLogger(__name__)
WEB_CONVERSATION_TITLE = "Сайт и сервер"


class Messenger:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        bus: EventBus,
        telegram: TelegramAPI | None,
        work_telegram: TelegramAPI | None = None,
    ):
        self.settings = settings
        self.db = db
        self.bus = bus
        self.telegram = telegram
        self.work_telegram = work_telegram
        self.sent: list[str] = []  # recent messages (for tests and debugging)

    async def send(self, text: str, title: str = "Атлас") -> None:
        self.sent = (self.sent + [text])[-50:]
        if self.telegram and self.settings.telegram_chat_ids:
            delivered = False
            for chat in self.settings.telegram_chat_ids:
                try:
                    await self.telegram.send_message(chat, text)
                    delivered = True
                except TelegramError as e:
                    log.warning("telegram send failed: %s", e)
            if delivered:
                return
        await self._to_web(text, title)

    async def send_work(self, text: str, title: str = "Forbsa") -> None:
        """Everything about the site (leads, alerts, chores, reports) goes to the
        work bot when it is configured. If it cannot deliver to anyone, fall back
        to the main route so nothing is lost."""
        if self.work_telegram and self.settings.work_chat_ids:
            delivered = False
            for chat in self.settings.work_chat_ids:
                try:
                    await self.work_telegram.send_message(chat, text)
                    delivered = True
                except TelegramError as e:
                    log.warning("work bot send failed: %s", e)
            if delivered:
                self.sent = (self.sent + [text])[-50:]
                return
        await self.send(text, title=title)

    async def send_lead(self, text: str) -> None:
        await self.send_work(text, title="Новая заявка")

    async def send_content(self, text: str) -> None:
        """Content-plan notifications go to the content-only recipients when set
        (through the main bot), otherwise to the owner like everything else."""
        ids = self.settings.content_chat_ids
        if self.telegram and ids:
            delivered = False
            for chat in ids:
                try:
                    await self.telegram.send_message(chat, text)
                    delivered = True
                except TelegramError as e:
                    log.warning("content notification failed: %s", e)
            if delivered:
                self.sent = (self.sent + [text])[-50:]
                return
        await self.send(text, title="Контент-план")

    async def _to_web(self, text: str, title: str) -> None:
        async with self.db.session() as s:
            conv = await s.scalar(select(Conversation).where(Conversation.title == WEB_CONVERSATION_TITLE).limit(1))
            if conv is None:
                conv = await create_conversation(s, WEB_CONVERSATION_TITLE)
            msg = Message(conversation_id=conv.id, role="assistant", text=text, api_content=None, provider="system")
            s.add(msg)
            touch(conv)
            await s.flush()
            n = Notification(kind="monitor", title=title, body=text[:500], conversation_id=conv.id, message_id=msg.id)
            s.add(n)
            await s.commit()
            out = message_out(msg)
            nid = n.id
        self.bus.publish(
            {
                "type": "notification",
                "notification": {"id": nid, "kind": "monitor", "title": title, "body": text[:500], "overdue": False, "conversation_id": conv.id},
                "message": out,
            }
        )
