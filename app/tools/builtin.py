"""The complete set of tools available to the agent in stage 1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services import items
from app.services.recurrence import WEEKDAY_CODES
from app.services.timeparse import describe_now, parse_datetime, parse_due, to_local_iso
from app.tools.registry import Tool, ToolContext, ToolOutcome, ToolRegistry

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- time


class NoArgs(_Args):
    pass


async def get_current_time(_: NoArgs, ctx: ToolContext) -> ToolOutcome:
    return ToolOutcome(
        {"now": to_local_iso(ctx.now, ctx.tz), "description": describe_now(ctx.now, ctx.tz)}
    )


# ----------------------------------------------------------------- reminders


class CreateReminderArgs(_Args):
    text: str = Field(min_length=1, max_length=500, description="Что напомнить, коротко.")
    when: str = Field(
        description="Время первого срабатывания в ISO 8601 с часовым поясом, "
        "например 2026-09-30T10:00:00+03:00."
    )
    recurrence: Literal["none", "daily", "weekly", "monthly", "yearly"] = Field(
        default="none", description="Повторение: none, daily, weekly (по дням недели), monthly, yearly."
    )
    interval_months: int = Field(
        default=1, ge=1, le=12, description="Для monthly: раз в N месяцев (3 = раз в квартал)."
    )
    weekdays: list[Weekday] | None = Field(
        default=None, description="Для weekly: дни недели, например [\"mon\", \"wed\"]."
    )


async def create_reminder(a: CreateReminderArgs, ctx: ToolContext) -> ToolOutcome:
    when = parse_datetime(a.when, ctx.tz)
    weekdays = [WEEKDAY_CODES.index(d) for d in a.weekdays] if a.weekdays else None
    r = await items.create_reminder(
        ctx.session,
        text=a.text,
        when=when,
        tz=ctx.tz,
        kind=a.recurrence,
        weekdays=weekdays,
        conversation_id=ctx.conversation_id,
        now=ctx.now,
        interval=a.interval_months,
    )
    out = ToolOutcome({"created": items.reminder_for_model(r, ctx.tz)}, card=items.reminder_card(r, ctx.tz))
    if ctx.scheduler:
        out.after_commit.append(lambda rid=r.id, at=r.next_fire_at: ctx.scheduler.schedule(rid, at))
    return out


class ListRemindersArgs(_Args):
    status: Literal["active", "done", "cancelled", "all"] = "active"


async def list_reminders(a: ListRemindersArgs, ctx: ToolContext) -> ToolOutcome:
    rs = await items.list_reminders(ctx.session, a.status, limit=50)
    return ToolOutcome(
        {"reminders": [items.reminder_for_model(r, ctx.tz) for r in rs]},
        card={"type": "reminder_list", "items": [items.reminder_card(r, ctx.tz) for r in rs]},
    )


class IdArgs(_Args):
    id: int = Field(ge=1)


async def cancel_reminder(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
    r = await items.cancel_reminder(ctx.session, a.id)
    out = ToolOutcome({"cancelled": items.reminder_for_model(r, ctx.tz)}, card=items.reminder_card(r, ctx.tz))
    if ctx.scheduler:
        out.after_commit.append(lambda rid=r.id: ctx.scheduler.unschedule(rid))
    return out


# --------------------------------------------------------------------- tasks


class CreateTaskArgs(_Args):
    title: str = Field(min_length=1, max_length=300)
    due: str | None = Field(
        default=None,
        description="Срок: дата YYYY-MM-DD или дата-время ISO 8601 с часовым поясом.",
    )
    notes: str | None = Field(default=None, max_length=4000)


async def create_task(a: CreateTaskArgs, ctx: ToolContext) -> ToolOutcome:
    due_at, has_time = parse_due(a.due, ctx.tz) if a.due else (None, False)
    t = await items.create_task(ctx.session, title=a.title, due_at=due_at, due_has_time=has_time, notes=a.notes)
    return ToolOutcome({"created": items.task_for_model(t, ctx.tz)}, card=items.task_card(t))


class ListTasksArgs(_Args):
    status: Literal["open", "done", "all"] = "open"


async def list_tasks(a: ListTasksArgs, ctx: ToolContext) -> ToolOutcome:
    ts = await items.list_tasks(ctx.session, a.status, limit=50)
    return ToolOutcome(
        {"tasks": [items.task_for_model(t, ctx.tz) for t in ts]},
        card={"type": "task_list", "items": [items.task_card(t) for t in ts]},
    )


async def complete_task(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
    t = await items.set_task_done(ctx.session, a.id, True)
    return ToolOutcome({"completed": items.task_for_model(t, ctx.tz)}, card=items.task_card(t))


class UpdateTaskArgs(_Args):
    id: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    due: str | None = Field(default=None, description="Новый срок: YYYY-MM-DD или ISO 8601.")
    clear_due: bool = Field(default=False, description="true, чтобы убрать срок.")
    notes: str | None = Field(default=None, max_length=4000)


async def update_task(a: UpdateTaskArgs, ctx: ToolContext) -> ToolOutcome:
    due = None
    if a.clear_due:
        due = (None, False)
    elif a.due:
        due = parse_due(a.due, ctx.tz)
    t = await items.update_task(ctx.session, a.id, title=a.title, notes=a.notes, due=due)
    return ToolOutcome({"updated": items.task_for_model(t, ctx.tz)}, card=items.task_card(t))


# -------------------------------------------------------------------- memory


class RememberFactArgs(_Args):
    text: str = Field(min_length=1, max_length=500, description="Факт о пользователе, одной фразой.")


async def remember_fact(a: RememberFactArgs, ctx: ToolContext) -> ToolOutcome:
    f = await items.remember_fact(ctx.session, a.text)
    return ToolOutcome({"saved": {"id": f.id, "text": f.text}}, card=items.fact_card(f))


async def list_facts(_: NoArgs, ctx: ToolContext) -> ToolOutcome:
    fs = await items.list_facts(ctx.session)
    return ToolOutcome(
        {"facts": [{"id": f.id, "text": f.text} for f in fs]},
        card={"type": "fact_list", "items": [items.fact_card(f) for f in fs]},
    )


async def forget_fact(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
    f = await items.forget_fact(ctx.session, a.id)
    return ToolOutcome({"forgotten": {"id": f.id, "text": f.text}}, card={"type": "fact_forgotten", "id": f.id, "text": f.text})


# ------------------------------------------------------------------ registry


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in [
        Tool("get_current_time", "Текущие дата и время в часовом поясе пользователя.", NoArgs, get_current_time, "Смотрю на часы…"),
        Tool(
            "create_reminder",
            "Создать напоминание. Время указывай в ISO 8601 с часовым поясом пользователя. "
            "Для повторяющихся напоминаний when задаёт первое срабатывание и время суток.",
            CreateReminderArgs,
            create_reminder,
            "Создаю напоминание…",
        ),
        Tool("list_reminders", "Список напоминаний (по умолчанию активные).", ListRemindersArgs, list_reminders, "Смотрю напоминания…"),
        Tool("cancel_reminder", "Отменить активное напоминание по id.", IdArgs, cancel_reminder, "Отменяю напоминание…"),
        Tool("create_task", "Создать задачу, при необходимости со сроком и заметками.", CreateTaskArgs, create_task, "Добавляю задачу…"),
        Tool("list_tasks", "Список задач (по умолчанию открытые).", ListTasksArgs, list_tasks, "Смотрю задачи…"),
        Tool("complete_task", "Отметить задачу выполненной по id.", IdArgs, complete_task, "Отмечаю задачу…"),
        Tool("update_task", "Изменить название, срок или заметки задачи.", UpdateTaskArgs, update_task, "Обновляю задачу…"),
        Tool(
            "remember_fact",
            "Сохранить в долговременную память устойчивый факт о пользователе "
            "(предпочтения, важные люди, привычки). Только если пользователь просит "
            "запомнить или это явно полезно надолго.",
            RememberFactArgs,
            remember_fact,
            "Запоминаю…",
        ),
        Tool("list_facts", "Показать все сохранённые факты о пользователе.", NoArgs, list_facts, "Вспоминаю…"),
        Tool("forget_fact", "Удалить факт из памяти по id.", IdArgs, forget_fact, "Забываю…"),
    ]:
        reg.register(tool)
    return reg
