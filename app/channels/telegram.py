"""Telegram channel: Bot API over plain HTTPS (long polling, no public URL).

Only chat ids listed in TELEGRAM_ALLOWED_CHAT_IDS are served; anything else is
ignored silently and logged without the message text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.channels.base import NotificationPayload, Notifier
from app.config import Settings
from app.db.session import Database
from app.services import kv

log = logging.getLogger(__name__)

MAX_LEN = 4000  # Telegram limit is 4096 characters per message


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str, base: str = "https://api.telegram.org", client: httpx.AsyncClient | None = None):
        self._url = f"{base.rstrip('/')}/bot{token}/"
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=15.0))

    async def call(self, method: str, **params) -> object:
        try:
            r = await self.client.post(self._url + method, json=params)
        except httpx.HTTPError as e:
            # never log the URL: it contains the bot token
            raise TelegramError(f"{method}: {e.__class__.__name__}") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description', '')}")
        return data.get("result")

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        chunks = [text[i : i + MAX_LEN] for i in range(0, max(len(text), 1), MAX_LEN)]
        for i, chunk in enumerate(chunks):
            extra = {"reply_markup": reply_markup} if reply_markup and i == len(chunks) - 1 else {}
            await self.call("sendMessage", chat_id=chat_id, text=chunk, disable_web_page_preview=True, **extra)

    async def set_menu_button(self, chat_id: int, text: str, url: str) -> None:
        await self.call(
            "setChatMenuButton", chat_id=chat_id, menu_button={"type": "web_app", "text": text, "web_app": {"url": url}}
        )

    async def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        res = await self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
        return res if isinstance(res, list) else []

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass
class BotReply:
    """A reply with an optional button that opens the Mini App."""

    text: str
    web_app: tuple[str, str] | None = None  # (button label, url)

    def markup(self) -> dict | None:
        if not self.web_app:
            return None
        label, url = self.web_app
        return {"inline_keyboard": [[{"text": label, "web_app": {"url": url}}]]}


Handler = Callable[[int, str], Awaitable[str | BotReply | None]]


class TelegramBot:
    """Long-polling loop that dispatches allowed messages to a handler."""

    OFFSET_KEY = "telegram_offset"

    def __init__(self, api: TelegramAPI, settings: Settings, db: Database, handler: Handler):
        self.api = api
        self.settings = settings
        self.allowed = settings.telegram_chat_ids
        self.content_only = settings.content_chat_ids
        self.db = db
        self.handler = handler
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="telegram-bot")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _setup_menu(self) -> None:
        """The button next to the input field opens Atlas (or only the content plan) as a Mini App."""
        if not self.settings.miniapp_url:
            return
        buttons = [(c, "Атлас", self.settings.miniapp_url) for c in self.allowed]
        buttons += [(c, "Контент-план", self.settings.content_miniapp_url) for c in self.content_only]
        for chat, text, url in buttons:
            try:
                await self.api.set_menu_button(chat, text, url)
            except TelegramError as e:
                log.warning("telegram menu button: %s", e)

    def content_reply(self) -> BotReply:
        """The only reply a content-only chat gets: the way into the content plan."""
        url = self.settings.content_miniapp_url
        if not url:
            return BotReply("Здесь будут приходить напоминания по контент-плану.")
        return BotReply("Контент-план открывается кнопкой ниже. Сюда приходят напоминания: что снимаем сегодня и когда публикация.", ("Открыть контент-план", url))

    async def _run(self) -> None:
        await self._setup_menu()
        async with self.db.session() as s:
            offset = int(await kv.get(s, self.OFFSET_KEY, 0) or 0)
        backoff = 5
        while True:
            try:
                updates = await self.api.get_updates(offset)
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("telegram polling failed: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for upd in updates:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                await self.process_update(upd)
            if updates:
                async with self.db.session() as s:
                    await kv.put(s, self.OFFSET_KEY, offset)
                    await s.commit()

    async def process_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text")
        if chat_id in self.content_only:
            if msg.get("text"):
                reply = self.content_reply()
                try:
                    markup = reply.markup()
                    if markup:
                        await self.api.send_message(chat_id, reply.text, markup)
                    else:
                        await self.api.send_message(chat_id, reply.text)
                except TelegramError as e:
                    log.warning("telegram send failed: %s", e)
            return
        if chat_id not in self.allowed:
            log.info("telegram: ignored message from unknown chat")
            return
        if not text:
            return
        try:
            reply = await self.handler(chat_id, text)
        except Exception:  # noqa: BLE001
            log.exception("telegram handler failed")
            reply = "Внутренняя ошибка. Подробности в логах сервера."
        if isinstance(reply, str):
            reply = BotReply(reply)
        if reply and reply.text:
            try:
                markup = reply.markup()
                if markup:
                    await self.api.send_message(chat_id, reply.text, markup)
                else:
                    await self.api.send_message(chat_id, reply.text)
            except TelegramError as e:
                log.warning("telegram send failed: %s", e)


class TelegramNotifier(Notifier):
    """Delivers fired reminders to Telegram as well."""

    name = "telegram"

    def __init__(self, api: TelegramAPI, chat_ids: set[int]):
        self.api = api
        self.chat_ids = chat_ids

    async def send(self, payload: NotificationPayload) -> None:
        prefix = "Просрочено" if payload.overdue else "Напоминание"
        for chat in self.chat_ids:
            await self.api.send_message(chat, f"{prefix}: {payload.body}")
