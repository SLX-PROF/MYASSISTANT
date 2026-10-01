"""Read-only agent tools for site duty (registered only when monitoring is on)."""

from __future__ import annotations

from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.services.timeparse import to_local_iso
from app.tools.builtin import NoArgs
from app.tools.registry import Tool, ToolContext, ToolOutcome


def monitor_tools(monitor: MonitorService, regular: RegularTasks) -> list[Tool]:
    async def get_site_status(_: NoArgs, ctx: ToolContext) -> ToolOutcome:
        snap = monitor.checks_snapshot()
        snap["active_alerts"] = [
            {"title": a.title, "level": a.level, "since": to_local_iso(a.started_at, ctx.tz)} for a in await monitor.alerts.active()
        ]
        return ToolOutcome(snap)

    async def list_regular_tasks(_: NoArgs, ctx: ToolContext) -> ToolOutcome:
        text = await regular.due_report(days=31)
        return ToolOutcome({"report": text, "note": "Подтверждение выполнения: команда /done <ключ> в Telegram."})

    return [
        Tool(
            "get_site_status",
            "Текущее состояние сайта и сервера по последним проверкам (только чтение).",
            NoArgs,
            get_site_status,
            "Смотрю состояние сайта…",
        ),
        Tool(
            "list_regular_tasks",
            "Регулярные задачи по сайту на ближайший месяц и просроченные (только чтение).",
            NoArgs,
            list_regular_tasks,
            "Смотрю регулярные задачи…",
        ),
    ]
