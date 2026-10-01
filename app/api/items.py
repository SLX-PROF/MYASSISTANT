"""Manual CRUD for reminders, tasks and memory facts (pages outside the chat)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.services import items
from app.services.recurrence import WEEKDAY_CODES
from app.services.timeparse import TimeParseError, parse_datetime, parse_due
from app.security import require_session

router = APIRouter(prefix="/api", tags=["items"], dependencies=[Depends(require_session)])


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------ reminders


class ReminderIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    when: str
    recurrence: Literal["none", "daily", "weekly", "monthly", "yearly"] = "none"
    weekdays: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] | None = None
    interval_months: int = Field(default=1, ge=1, le=12)


@router.get("/reminders")
async def list_reminders(request: Request, status: Literal["active", "done", "cancelled", "all"] = "active"):
    tz = request.app.state.settings.tz
    async with request.app.state.db.session() as s:
        return [items.reminder_card(r, tz) for r in await items.list_reminders(s, status, limit=500)]


@router.post("/reminders")
async def create_reminder(body: ReminderIn, request: Request):
    st = request.app.state
    tz = st.settings.tz
    try:
        when = parse_datetime(body.when, tz)
        async with st.db.session() as s:
            r = await items.create_reminder(
                s,
                text=body.text,
                when=when,
                tz=tz,
                kind=body.recurrence,
                weekdays=[WEEKDAY_CODES.index(d) for d in body.weekdays] if body.weekdays else None,
                interval=body.interval_months,
            )
            await s.commit()
    except (items.ItemError, TimeParseError, ValueError) as e:
        raise _bad(e) from e
    st.scheduler.schedule(r.id, r.next_fire_at)
    return items.reminder_card(r, tz)


@router.post("/reminders/{rid}/cancel")
async def cancel_reminder(rid: int, request: Request):
    st = request.app.state
    try:
        async with st.db.session() as s:
            r = await items.cancel_reminder(s, rid)
            await s.commit()
    except items.ItemError as e:
        raise _bad(e) from e
    st.scheduler.unschedule(rid)
    return items.reminder_card(r, st.settings.tz)


# ---------------------------------------------------------------------- tasks


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    due: str | None = None
    clear_due: bool = False
    done: bool | None = None


@router.get("/tasks")
async def list_tasks(request: Request, status: Literal["open", "done", "all"] = "open"):
    async with request.app.state.db.session() as s:
        return [items.task_card(t) for t in await items.list_tasks(s, status, limit=1000)]


@router.post("/tasks")
async def create_task(body: TaskIn, request: Request):
    tz = request.app.state.settings.tz
    try:
        due_at, has_time = parse_due(body.due, tz) if body.due else (None, False)
        async with request.app.state.db.session() as s:
            t = await items.create_task(s, title=body.title, due_at=due_at, due_has_time=has_time, notes=body.notes)
            await s.commit()
    except (items.ItemError, TimeParseError) as e:
        raise _bad(e) from e
    return items.task_card(t)


@router.patch("/tasks/{tid}")
async def patch_task(tid: int, body: TaskPatch, request: Request):
    tz = request.app.state.settings.tz
    try:
        due = (None, False) if body.clear_due else (parse_due(body.due, tz) if body.due else None)
        async with request.app.state.db.session() as s:
            t = await items.update_task(s, tid, title=body.title, notes=body.notes, due=due)
            if body.done is not None:
                t = await items.set_task_done(s, tid, body.done)
            await s.commit()
    except (items.ItemError, TimeParseError) as e:
        raise _bad(e) from e
    return items.task_card(t)


@router.delete("/tasks/{tid}")
async def delete_task(tid: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            await items.delete_task(s, tid)
            await s.commit()
    except items.ItemError as e:
        raise _bad(e) from e
    return {"ok": True}


# ---------------------------------------------------------------------- facts


class FactIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.get("/facts")
async def list_facts(request: Request):
    async with request.app.state.db.session() as s:
        return [items.fact_card(f) for f in await items.list_facts(s)]


@router.post("/facts")
async def create_fact(body: FactIn, request: Request):
    try:
        async with request.app.state.db.session() as s:
            f = await items.remember_fact(s, body.text)
            await s.commit()
    except items.ItemError as e:
        raise _bad(e) from e
    return items.fact_card(f)


@router.delete("/facts/{fid}")
async def delete_fact(fid: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            await items.forget_fact(s, fid)
            await s.commit()
    except items.ItemError as e:
        raise _bad(e) from e
    return {"ok": True}
