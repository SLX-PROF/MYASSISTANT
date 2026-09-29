"""Conversation helpers and serialisation shared by the API, agent and scheduler."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, utcnow
from app.services.timeparse import to_utc_iso

DEFAULT_TITLE = "Новый чат"


def message_out(m: Message) -> dict:
    """Message as seen by the UI (never includes raw model content)."""
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "role": m.role,
        "text": m.text,
        "cards": m.cards or [],
        "created_at": to_utc_iso(m.created_at),
    }


def conversation_out(c: Conversation, preview: str | None = None, unread: int = 0) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "created_at": to_utc_iso(c.created_at),
        "updated_at": to_utc_iso(c.updated_at),
        "preview": preview,
        "unread": unread,
    }


async def create_conversation(s: AsyncSession, title: str = DEFAULT_TITLE) -> Conversation:
    c = Conversation(title=title)
    s.add(c)
    await s.flush()
    return c


async def notification_target(s: AsyncSession, preferred_id: int | None) -> Conversation:
    """Where a reminder shows up: the chat it was created in, else the latest chat."""
    if preferred_id is not None:
        c = await s.get(Conversation, preferred_id)
        if c is not None:
            return c
    c = await s.scalar(select(Conversation).order_by(Conversation.updated_at.desc()).limit(1))
    return c or await create_conversation(s, "Напоминания")


def title_from_text(text: str, limit: int = 60) -> str:
    t = " ".join(text.split())
    if len(t) <= limit:
        return t or DEFAULT_TITLE
    cut = t[:limit].rsplit(" ", 1)[0]
    return (cut or t[:limit]) + "…"


def touch(c: Conversation) -> None:
    c.updated_at = utcnow()
