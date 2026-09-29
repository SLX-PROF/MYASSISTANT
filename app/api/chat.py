"""Conversations, messages and the streaming chat endpoint."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.agent import ConversationNotFound
from app.db.models import Conversation, Message, Notification
from app.security import require_session
from app.services.chat import conversation_out, create_conversation, message_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["chat"], dependencies=[Depends(require_session)])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


@router.get("")
async def list_conversations(request: Request, limit: int = Query(100, le=500), offset: int = 0):
    async with request.app.state.db.session() as s:
        convs = (
            await s.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit).offset(offset))
        ).all()
        ids = [c.id for c in convs]
        previews: dict[int, str] = {}
        unread: dict[int, int] = {}
        if ids:
            last_ids = (
                select(func.max(Message.id))
                .where(Message.conversation_id.in_(ids), Message.text != "")
                .group_by(Message.conversation_id)
            )
            for m in (await s.scalars(select(Message).where(Message.id.in_(last_ids)))).all():
                previews[m.conversation_id] = m.text[:120]
            rows = await s.execute(
                select(Notification.conversation_id, func.count())
                .where(Notification.read_at.is_(None), Notification.conversation_id.in_(ids))
                .group_by(Notification.conversation_id)
            )
            unread = {cid: n for cid, n in rows.all()}
    return [conversation_out(c, previews.get(c.id), unread.get(c.id, 0)) for c in convs]


@router.post("")
async def new_conversation(request: Request, body: ConversationIn | None = None):
    async with request.app.state.db.session() as s:
        c = await create_conversation(s, (body.title if body and body.title else None) or "Новый чат")
        await s.commit()
        return conversation_out(c)


@router.patch("/{cid}")
async def rename_conversation(cid: int, body: ConversationPatch, request: Request):
    async with request.app.state.db.session() as s:
        c = await s.get(Conversation, cid)
        if c is None:
            raise HTTPException(404, "Диалог не найден")
        c.title = body.title.strip()
        await s.commit()
        return conversation_out(c)


@router.delete("/{cid}")
async def delete_conversation(cid: int, request: Request):
    if cid in request.app.state.active_runs:
        raise HTTPException(409, "Дождитесь окончания ответа")
    async with request.app.state.db.session() as s:
        c = await s.get(Conversation, cid)
        if c is None:
            raise HTTPException(404, "Диалог не найден")
        await s.delete(c)
        await s.commit()
    return {"ok": True}


@router.get("/{cid}/messages")
async def get_messages(
    cid: int, request: Request, before_id: int | None = None, limit: int = Query(50, ge=1, le=200)
):
    """Newest page first in DB, returned oldest->newest. Use before_id to page back."""
    async with request.app.state.db.session() as s:
        if await s.get(Conversation, cid) is None:
            raise HTTPException(404, "Диалог не найден")
        q = select(Message).where(Message.conversation_id == cid)
        if before_id:
            q = q.where(Message.id < before_id)
        rows = (await s.scalars(q.order_by(Message.id.desc()).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return {
        "messages": [message_out(m) for m in rows if m.role != "tool" or m.cards],
        "has_more": has_more,
        "running": cid in request.app.state.active_runs,
    }


@router.post("/{cid}/messages")
async def send_message(cid: int, body: MessageIn, request: Request):
    """Run the agent; stream progress as Server-Sent Events.

    The agent runs as a background task: if the tab closes mid-answer the
    answer is still completed and saved.
    """
    state = request.app.state
    async with state.db.session() as s:
        if await s.get(Conversation, cid) is None:
            raise HTTPException(404, "Диалог не найден")
    if cid in state.active_runs:
        raise HTTPException(409, "Ассистент ещё отвечает в этом диалоге")
    state.active_runs.add(cid)

    queue: asyncio.Queue = asyncio.Queue()

    async def worker():
        try:
            async for ev in state.agent.run(cid, body.text.strip()):
                await queue.put(ev)
        except ConversationNotFound:
            await queue.put({"type": "error", "message": "Диалог не найден"})
        except Exception:  # noqa: BLE001
            log.exception("agent run failed")
            await queue.put({"type": "error", "message": "Внутренняя ошибка. Подробности в логах сервера."})
        finally:
            state.active_runs.discard(cid)
            state.bus.publish({"type": "conversation_updated", "conversation_id": cid})
            await queue.put(None)

    task = asyncio.create_task(worker())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)

    async def stream():
        while True:
            ev = await queue.get()
            if ev is None:
                break
            yield sse(ev)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
