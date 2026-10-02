"""Mail API: inventory of services, one-click unsubscribe, digest on demand."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import MailService, utcnow
from app.mail.imap import MailError
from app.mail.inventory import CATEGORIES, service_out
from app.mail.unsubscribe import UnsubscribeError, one_click
from app.security import require_session

router = APIRouter(prefix="/api/mail", tags=["mail"], dependencies=[Depends(require_session)])


def _mail(request: Request):
    m = getattr(request.app.state, "mail", None)
    if m is None:
        raise HTTPException(400, "Почта не подключена: впишите MAIL_ACCOUNTS в .env и перезапустите Атлас.")
    return m


@router.get("/status")
async def status(request: Request):
    m = getattr(request.app.state, "mail", None)
    if m is None:
        return {"enabled": False}
    inv = m["inventory"]
    progress = await inv.load_state()
    return {
        "enabled": True,
        "accounts": [a.address for a in inv.accounts()],
        "busy": inv.busy,
        "progress": progress,
        "categories": CATEGORIES,
        "digest_time": request.app.state.settings.mail_digest_time,
    }


@router.post("/collect")
async def collect(request: Request):
    """Step 1 (free): read headers of the whole history and group by sender."""
    inv = _mail(request)["inventory"]
    try:
        inv.start(inv.collect())
    except MailError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/classify")
async def classify(request: Request):
    """Step 2 (costs money, see the estimate): the model sorts the list."""
    inv = _mail(request)["inventory"]
    try:
        inv.start(inv.classify())
    except MailError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.get("/services")
async def services(request: Request):
    async with request.app.state.db.session() as s:
        rows = (await s.scalars(select(MailService).order_by(MailService.count.desc()))).all()
        return [service_out(r) for r in rows]


class ServicePatch(BaseModel):
    status: Literal["new", "keep", "unsubscribed", "done", "hidden"]


@router.patch("/services/{sid}")
async def patch_service(sid: int, body: ServicePatch, request: Request):
    async with request.app.state.db.session() as s:
        r = await s.get(MailService, sid)
        if r is None:
            raise HTTPException(404, "Нет такого сервиса")
        r.status, r.updated_at = body.status, utcnow()
        await s.commit()
        return service_out(r)


@router.post("/services/{sid}/unsubscribe")
async def unsubscribe(sid: int, request: Request):
    async with request.app.state.db.session() as s:
        r = await s.get(MailService, sid)
        if r is None:
            raise HTTPException(404, "Нет такого сервиса")
        if not (r.one_click and r.unsubscribe.lower().startswith("https://")):
            raise HTTPException(400, "Здесь нет отписки в один клик — откройте ссылку отписки вручную.")
        try:
            await one_click(r.unsubscribe)
        except UnsubscribeError as e:
            raise HTTPException(502, str(e)) from e
        r.status, r.updated_at = "unsubscribed", utcnow()
        await s.commit()
        return service_out(r)


@router.post("/digest")
async def digest_now(request: Request):
    """Build today's digest right now (does not move the «already reported» mark)."""
    m = _mail(request)
    letters, _states, errors = await m["digest"].collect(utcnow())
    text = await m["digest"].render(letters, errors)
    return {"text": text or "Новых писем нет."}
