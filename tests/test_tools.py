from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Reminder, Task
from app.tools.builtin import build_registry
from app.tools.registry import ToolContext

NOW = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
async def ctx(db, settings):
    async with db.session() as s:
        yield ToolContext(session=s, settings=settings, now=NOW)


def test_only_allowed_tools(registry):
    assert sorted(registry.names()) == sorted(
        [
            "get_current_time",
            "create_reminder",
            "list_reminders",
            "cancel_reminder",
            "create_task",
            "list_tasks",
            "complete_task",
            "update_task",
            "remember_fact",
            "list_facts",
            "forget_fact",
        ]
    )


def test_schemas_are_inlined_and_keep_field_names(registry):
    specs = {s.name: s for s in registry.specs()}
    task_schema = specs["create_task"].input_schema
    assert "title" in task_schema["properties"]  # a field literally named "title"
    assert "$defs" not in str(task_schema) and "$ref" not in str(task_schema)
    assert specs["get_current_time"].input_schema["properties"] == {}


async def test_unknown_tool(registry, ctx):
    out = await registry.execute("run_shell", {"cmd": "ls"}, ctx)
    assert out.is_error and "Неизвестный" in out.data["error"]


@pytest.mark.parametrize(
    "args, fragment",
    [
        ({}, "text"),
        ({"text": "x"}, "when"),
        ({"text": "x", "when": "2026-10-01T10:00:00+03:00", "extra": 1}, "extra"),
        ({"text": "x", "when": "2026-10-01T10:00:00+03:00", "recurrence": "yearly"}, "recurrence"),
        ({"text": "x", "when": "2026-10-01T10:00:00+03:00", "weekdays": ["monday"]}, "weekdays"),
    ],
)
async def test_create_reminder_validation(registry, ctx, args, fragment):
    out = await registry.execute("create_reminder", args, ctx)
    assert out.is_error
    assert fragment in out.data["error"]


async def test_create_reminder_bad_time_format(registry, ctx):
    out = await registry.execute("create_reminder", {"text": "x", "when": "завтра в 10"}, ctx)
    assert out.is_error and "ISO 8601" in out.data["error"]


async def test_create_reminder_in_past_is_rejected(registry, ctx):
    past = (NOW - timedelta(hours=1)).isoformat()
    out = await registry.execute("create_reminder", {"text": "x", "when": past}, ctx)
    assert out.is_error and "прошло" in out.data["error"]


async def test_create_reminder_ok_and_naive_time_uses_user_tz(registry, ctx):
    out = await registry.execute("create_reminder", {"text": "Оплатить интернет", "when": "2026-09-30T10:00:00"}, ctx)
    assert not out.is_error, out.data
    assert out.card["type"] == "reminder"
    r = await ctx.session.get(Reminder, out.card["id"])
    assert r.next_fire_at == datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)
    assert out.data["created"]["next_fire_at"] == "2026-09-30T10:00+03:00"


async def test_weekly_reminder_first_fire_on_selected_day(registry, ctx):
    # 2026-09-29 is a Tuesday; asking for Mondays must not fire on Tuesday.
    out = await registry.execute(
        "create_reminder",
        {"text": "Спортзал", "when": "2026-09-29T19:00:00+03:00", "recurrence": "weekly", "weekdays": ["mon"]},
        ctx,
    )
    assert not out.is_error, out.data
    r = await ctx.session.get(Reminder, out.card["id"])
    assert r.next_fire_at.astimezone(ctx.tz).weekday() == 0


async def test_task_lifecycle(registry, ctx):
    out = await registry.execute("create_task", {"title": "Купить продукты", "due": "2026-09-30"}, ctx)
    assert not out.is_error, out.data
    tid = out.card["id"]
    assert out.data["created"]["due"] == "2026-09-30"
    t = await ctx.session.get(Task, tid)
    assert t.due_has_time is False

    bad = await registry.execute("update_task", {"id": tid, "due": "30.09.2026"}, ctx)
    assert bad.is_error

    upd = await registry.execute("update_task", {"id": tid, "title": "Купить продукты и кофе"}, ctx)
    assert upd.data["updated"]["title"] == "Купить продукты и кофе"

    done = await registry.execute("complete_task", {"id": tid}, ctx)
    assert done.card["status"] == "done"

    missing = await registry.execute("complete_task", {"id": 999}, ctx)
    assert missing.is_error and "не найдена" in missing.data["error"]

    wrong_type = await registry.execute("complete_task", {"id": "abc"}, ctx)
    assert wrong_type.is_error


async def test_facts(registry, ctx):
    a = await registry.execute("remember_fact", {"text": "Пьёт кофе без сахара"}, ctx)
    b = await registry.execute("remember_fact", {"text": "пьёт кофе  без сахара"}, ctx)
    assert a.card["id"] == b.card["id"]  # deduplicated
    lst = await registry.execute("list_facts", {}, ctx)
    assert len(lst.data["facts"]) == 1
    gone = await registry.execute("forget_fact", {"id": a.card["id"]}, ctx)
    assert not gone.is_error
