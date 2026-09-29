"""Business logic for reminders, tasks and memory facts.

Used by both the agent tools and the REST API, so behaviour is identical
whether an item is created from chat or from the UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fact, Reminder, Task, utcnow
from app.services import recurrence as rec
from app.services.timeparse import to_local_iso, to_utc_iso


class ItemError(ValueError):
    """User-facing validation / lookup error (message is in Russian)."""


# --------------------------------------------------------------------- reminders

# Allow a small slack for "now" coming from the model's view of the clock.
PAST_TOLERANCE = timedelta(seconds=90)


async def create_reminder(
    s: AsyncSession,
    *,
    text: str,
    when: datetime,
    tz: ZoneInfo,
    kind: str = "none",
    weekdays: list[int] | None = None,
    conversation_id: int | None = None,
    now: datetime | None = None,
) -> Reminder:
    now = now or utcnow()
    text = text.strip()
    if not text:
        raise ItemError("Текст напоминания пустой.")
    rule = rec.build_rule(kind, when, tz, weekdays)
    first = when
    if when < now - PAST_TOLERANCE:
        if rule["kind"] == "none":
            raise ItemError("Это время уже прошло. Уточните дату и время напоминания.")
        first = rec.next_occurrence(rule, now)
    elif rule["kind"] == "weekly" and when.astimezone(tz).weekday() not in rule["weekdays"]:
        # first fire must fall on one of the selected weekdays
        first = rec.next_occurrence(rule, when - timedelta(seconds=1))
    r = Reminder(
        text=text[:500],
        next_fire_at=max(first, now) if rule["kind"] == "none" else first,
        recurrence=rule,
        status="active",
        conversation_id=conversation_id,
    )
    s.add(r)
    await s.flush()
    return r


async def list_reminders(s: AsyncSession, status: str | None = "active", limit: int = 100) -> list[Reminder]:
    q = select(Reminder)
    if status and status != "all":
        q = q.where(Reminder.status == status)
    q = q.order_by(Reminder.next_fire_at.is_(None), Reminder.next_fire_at, Reminder.id.desc()).limit(limit)
    return list((await s.scalars(q)).all())


async def get_reminder(s: AsyncSession, reminder_id: int) -> Reminder:
    r = await s.get(Reminder, reminder_id)
    if r is None:
        raise ItemError(f"Напоминание #{reminder_id} не найдено.")
    return r


async def cancel_reminder(s: AsyncSession, reminder_id: int) -> Reminder:
    r = await get_reminder(s, reminder_id)
    if r.status != "active":
        raise ItemError(f"Напоминание #{reminder_id} уже не активно.")
    r.status = "cancelled"
    r.next_fire_at = None
    return r


def reminder_card(r: Reminder, tz: ZoneInfo) -> dict:
    return {
        "type": "reminder",
        "id": r.id,
        "text": r.text,
        "status": r.status,
        "next_fire_at": to_utc_iso(r.next_fire_at),
        "recurrence": r.recurrence,
        "recurrence_label": rec.describe(r.recurrence),
        "last_fired_at": to_utc_iso(r.last_fired_at),
    }


def reminder_for_model(r: Reminder, tz: ZoneInfo) -> dict:
    return {
        "id": r.id,
        "text": r.text,
        "status": r.status,
        "next_fire_at": to_local_iso(r.next_fire_at, tz),
        "repeat": rec.describe(r.recurrence),
    }


# ------------------------------------------------------------------------- tasks


async def create_task(
    s: AsyncSession,
    *,
    title: str,
    due_at: datetime | None = None,
    due_has_time: bool = False,
    notes: str | None = None,
) -> Task:
    title = title.strip()
    if not title:
        raise ItemError("Название задачи пустое.")
    t = Task(
        title=title[:300],
        notes=(notes or "").strip() or None,
        due_at=due_at,
        due_has_time=due_has_time if due_at else False,
    )
    s.add(t)
    await s.flush()
    return t


async def list_tasks(s: AsyncSession, status: str | None = "open", limit: int = 200) -> list[Task]:
    q = select(Task)
    if status and status != "all":
        q = q.where(Task.status == status)
    if status == "done":
        q = q.order_by(Task.completed_at.desc())
    else:
        q = q.order_by(Task.status, Task.due_at.is_(None), Task.due_at, Task.id.desc())
    return list((await s.scalars(q.limit(limit))).all())


async def get_task(s: AsyncSession, task_id: int) -> Task:
    t = await s.get(Task, task_id)
    if t is None:
        raise ItemError(f"Задача #{task_id} не найдена.")
    return t


async def set_task_done(s: AsyncSession, task_id: int, done: bool = True) -> Task:
    t = await get_task(s, task_id)
    t.status = "done" if done else "open"
    t.completed_at = utcnow() if done else None
    t.updated_at = utcnow()
    return t


async def update_task(
    s: AsyncSession,
    task_id: int,
    *,
    title: str | None = None,
    notes: str | None = None,
    due: tuple[datetime | None, bool] | None = None,
) -> Task:
    t = await get_task(s, task_id)
    if title is not None:
        if not title.strip():
            raise ItemError("Название задачи не может быть пустым.")
        t.title = title.strip()[:300]
    if notes is not None:
        t.notes = notes.strip() or None
    if due is not None:
        t.due_at, t.due_has_time = due[0], (due[1] if due[0] else False)
    t.updated_at = utcnow()
    return t


async def delete_task(s: AsyncSession, task_id: int) -> None:
    await s.delete(await get_task(s, task_id))


def task_card(t: Task) -> dict:
    return {
        "type": "task",
        "id": t.id,
        "title": t.title,
        "notes": t.notes,
        "status": t.status,
        "due_at": to_utc_iso(t.due_at),
        "due_has_time": t.due_has_time,
        "completed_at": to_utc_iso(t.completed_at),
    }


def task_for_model(t: Task, tz: ZoneInfo) -> dict:
    due = None
    if t.due_at:
        due = to_local_iso(t.due_at, tz) if t.due_has_time else t.due_at.astimezone(tz).date().isoformat()
    return {"id": t.id, "title": t.title, "status": t.status, "due": due, "notes": t.notes}


# ------------------------------------------------------------------------- facts

MAX_FACTS = 200


async def remember_fact(s: AsyncSession, text: str) -> Fact:
    text = " ".join(text.split())
    if not text:
        raise ItemError("Факт пустой.")
    count = await s.scalar(select(func.count()).select_from(Fact))
    if (count or 0) >= MAX_FACTS:
        raise ItemError("Память заполнена. Удалите старые факты на странице «Память».")
    # SQLite's lower() is ASCII-only, so compare Cyrillic text in Python.
    key = text.casefold()
    for existing in await list_facts(s):
        if existing.text.casefold() == key:
            return existing
    f = Fact(text=text[:500])
    s.add(f)
    await s.flush()
    return f


async def list_facts(s: AsyncSession) -> list[Fact]:
    return list((await s.scalars(select(Fact).order_by(Fact.id))).all())


async def forget_fact(s: AsyncSession, fact_id: int) -> Fact:
    f = await s.get(Fact, fact_id)
    if f is None:
        raise ItemError(f"Факт #{fact_id} не найден.")
    await s.delete(f)
    return f


def fact_card(f: Fact) -> dict:
    return {"type": "fact", "id": f.id, "text": f.text, "created_at": to_utc_iso(f.created_at)}


def facts_block(facts: list[Fact], max_chars: int) -> str:
    """Compact memory text for the model. Newest facts win when over budget."""
    lines: list[str] = []
    used = 0
    for f in reversed(facts):
        line = f"- [#{f.id}] {f.text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    return "\n".join(lines)
