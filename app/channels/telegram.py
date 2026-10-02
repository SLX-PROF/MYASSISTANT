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
from app.services.voice import VoiceError

log = logging.getLogger(__name__)

MAX_LEN = 4000  # Telegram limit is 4096 characters per message


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str, base: str = "https://api.telegram.org", client: httpx.AsyncClient | None = None):
        self._url = f"{base.rstrip('/')}/bot{token}/"
        self._file_url = f"{base.rstrip('/')}/file/bot{token}/"
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

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action=action)
        except TelegramError:
            pass  # cosmetic

    async def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> None:
        try:
            r = await self.client.post(
                self._url + "sendDocument",
                data={"chat_id": str(chat_id), "caption": caption[:1000]},
                files={"document": (filename, data, "application/octet-stream")},
                timeout=httpx.Timeout(300.0, connect=15.0),
            )
            data_ = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TelegramError(f"sendDocument: {e.__class__.__name__}") from None
        if not data_.get("ok"):
            raise TelegramError(f"sendDocument: {data_.get('error_code')} {data_.get('description', '')}")

    async def download_file(self, file_id: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        """Bytes of a file the user sent (bots can fetch up to 20 MB)."""
        info = await self.call("getFile", file_id=file_id)
        path = (info or {}).get("file_path") if isinstance(info, dict) else None
        if not path:
            raise TelegramError("getFile: no file_path")
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError("getFile: file too large")
        try:
            r = await self.client.get(self._file_url + path)
        except httpx.HTTPError as e:
            raise TelegramError(f"download: {e.__class__.__name__}") from None
        if r.status_code != 200:
            raise TelegramError(f"download: HTTP {r.status_code}")
        return r.content[:max_bytes]

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
NoteSaver = Callable[[str, str], Awaitable[str]]  # (text, url) -> reply
VOICE_MAX_SECONDS = 300


def forwarded_text(msg: dict) -> tuple[str, str]:
    """Text and the first link of a forwarded post (links may hide behind words)."""
    text = msg.get("text") or msg.get("caption") or ""
    url = ""
    for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
        if ent.get("type") == "text_link" and ent.get("url"):
            url = ent["url"]
            break
    origin = msg.get("forward_origin") or {}
    chat = origin.get("chat") or origin.get("sender_chat") or {}
    src = chat.get("title") or (origin.get("sender_user") or {}).get("first_name") or origin.get("sender_user_name") or ""
    if chat.get("username") and origin.get("message_id"):
        url = url or f"https://t.me/{chat['username']}/{origin['message_id']}"
    if src:
        text = f"{text}\n\nИсточник: {src}".strip()
    return text, url


class TelegramBot:
    """Long-polling loop that dispatches allowed messages to a handler."""

    OFFSET_KEY = "telegram_offset"

    def __init__(
        self,
        api: TelegramAPI,
        settings: Settings,
        db: Database,
        handler: Handler,
        transcriber=None,
        note_saver: NoteSaver | None = None,
        allowed: set[int] | None = None,
        content_only: set[int] | None = None,
        offset_key: str = "telegram_offset",
        menu: bool = True,
        commands: list[tuple[str, str]] | None = None,
    ):
        self.api = api
        self.transcriber = transcriber
        self.note_saver = note_saver
        self.settings = settings
        self.allowed = settings.telegram_chat_ids if allowed is None else allowed
        self.content_only = settings.content_chat_ids if content_only is None else content_only
        self.OFFSET_KEY = offset_key
        self.menu = menu
        self.commands = commands
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
        # Polling does not work while a webhook is set (e.g. an old bot that ran other code).
        try:
            await self.api.call("deleteWebhook")
        except TelegramError as e:
            log.warning("telegram deleteWebhook: %s", e)
        if self.commands is not None:
            try:
                await self.api.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in self.commands])
            except TelegramError as e:
                log.warning("telegram setMyCommands: %s", e)
        if not self.menu or not self.settings.miniapp_url:
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
        if msg.get("forward_origin") or msg.get("forward_date"):
            if self.note_saver and (msg.get("text") or msg.get("caption")):
                note_text, url = forwarded_text(msg)
                await self._reply(chat_id, await self.note_saver(note_text, url))
                return
        voice = msg.get("voice") or msg.get("audio") or msg.get("video_note")
        if voice and not text:
            text = await self._voice_to_text(chat_id, voice)
            if not text:
                return
        if not text:
            return
        await self.api.send_chat_action(chat_id)
        try:
            reply = await self.handler(chat_id, text)
        except Exception:  # noqa: BLE001
            log.exception("telegram handler failed")
            reply = "Внутренняя ошибка. Подробности в логах сервера."
        await self._reply(chat_id, reply)

    async def _voice_to_text(self, chat_id: int, voice: dict) -> str:
        if self.transcriber is None:
            await self._reply(chat_id, "Голосовые выключены (VOICE_ENABLED=false). Напишите текстом.")
            return ""
        if int(voice.get("duration") or 0) > VOICE_MAX_SECONDS:
            await self._reply(chat_id, "Голосовое длиннее 5 минут — так много за раз не разберу. Разбейте на части.")
            return ""
        await self.api.send_chat_action(chat_id)
        try:
            audio = await self.api.download_file(voice["file_id"])
            text = await self.transcriber.transcribe(audio)
        except TelegramError as e:
            log.warning("voice download failed: %s", e)
            await self._reply(chat_id, "Не смог скачать голосовое, попробуйте ещё раз.")
            return ""
        except VoiceError as e:
            await self._reply(chat_id, str(e))
            return ""
        if not text:
            await self._reply(chat_id, "Не расслышал, повторите, пожалуйста.")
            return ""
        await self._reply(chat_id, f"Распознал: «{text}»")
        return text

    async def _reply(self, chat_id: int, reply: str | BotReply | None) -> None:
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
