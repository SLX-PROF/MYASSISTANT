"""Notifications, live event stream (SSE) and UI settings."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.db.models import Notification, Setting, utcnow
from app.security import require_session
from app.services.timeparse import to_utc_iso

router = APIRouter(prefix="/api", tags=["notifications"], dependencies=[Depends(require_session)])

KEEPALIVE_SECONDS = 20


def notification_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "overdue": n.overdue,
        "conversation_id": n.conversation_id,
        "message_id": n.message_id,
        "read": n.read_at is not None,
        "created_at": to_utc_iso(n.created_at),
    }


@router.get("/notifications")
async def list_notifications(request: Request, unread_only: bool = False, limit: int = 50):
    async with request.app.state.db.session() as s:
        q = select(Notification)
        if unread_only:
            q = q.where(Notification.read_at.is_(None))
        rows = (await s.scalars(q.order_by(Notification.id.desc()).limit(min(limit, 200)))).all()
    return [notification_out(n) for n in rows]


class MarkRead(BaseModel):
    ids: list[int] | None = None
    conversation_id: int | None = None
    all: bool = False


@router.post("/notifications/read")
async def mark_read(body: MarkRead, request: Request):
    q = update(Notification).where(Notification.read_at.is_(None))
    if body.ids:
        q = q.where(Notification.id.in_(body.ids))
    elif body.conversation_id is not None:
        q = q.where(Notification.conversation_id == body.conversation_id)
    elif not body.all:
        return {"updated": 0}
    async with request.app.state.db.session() as s:
        res = await s.execute(q.values(read_at=utcnow()))
        await s.commit()
    request.app.state.bus.publish({"type": "notifications_read"})
    return {"updated": res.rowcount}


@router.get("/events")
async def events(request: Request):
    """Live events for open tabs: notifications, conversation updates."""
    bus = request.app.state.bus

    async def stream():
        async with bus.subscribe() as q:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -------------------------------------------------------------- UI settings


class UISettings(BaseModel):
    theme: str = Field(default="dark", pattern="^(dark|light|system)$")
    accent: str = Field(default="#22d3ee", pattern="^#[0-9a-fA-F]{6}$")


@router.get("/settings/ui")
async def get_ui_settings(request: Request):
    async with request.app.state.db.session() as s:
        row = await s.get(Setting, "ui")
    return UISettings(**(row.value if row else {})).model_dump()


@router.put("/settings/ui")
async def put_ui_settings(body: UISettings, request: Request):
    async with request.app.state.db.session() as s:
        row = await s.get(Setting, "ui")
        if row is None:
            s.add(Setting(key="ui", value=body.model_dump()))
        else:
            row.value = body.model_dump()
            row.updated_at = utcnow()
        await s.commit()
    return body.model_dump()
