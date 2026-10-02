"""Agent tools for the personal finance planner (chat and Telegram)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from app.finance import service as fs
from app.tools.builtin import _Args
from app.tools.registry import Tool, ToolContext, ToolOutcome


def _today(ctx: ToolContext) -> date:
    return ctx.now.astimezone(ctx.tz).date()


def _card(text: str) -> dict:
    return {"type": "finance", "text": text}


def _kop(rubles: float) -> int:
    return round(rubles * 100)


def _date(value: str | None, ctx: ToolContext) -> date:
    if not value:
        return _today(ctx)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise fs.FinanceError("Дата в формате ГГГГ-ММ-ДД.") from None


class AddArgs(_Args):
    amount: float = Field(gt=0, le=100_000_000, description="Сумма в рублях.")
    kind: Literal["expense", "income"] = "expense"
    category: str | None = Field(default=None, description="Название категории; пусто — определю по описанию.")
    date: str | None = Field(default=None, description="YYYY-MM-DD; по умолчанию сегодня.")
    note: str = Field(default="", max_length=200, description="Что это: «такси», «зарплата».")


class MonthArgs(_Args):
    month: str | None = Field(default=None, description="YYYY-MM; по умолчанию текущий месяц.")


class ListArgs(_Args):
    month: str | None = Field(default=None, description="YYYY-MM; по умолчанию текущий месяц.")
    category: str | None = None


class IdArgs(_Args):
    id: int = Field(ge=1)


class LimitArgs(_Args):
    category: str
    monthly_limit: int = Field(ge=0, le=100_000_000, description="Лимит в рублях в месяц; 0 — без лимита.")


class RecurringArgs(_Args):
    title: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=100_000_000, description="Сумма в рублях.")
    day_of_month: int = Field(ge=1, le=31)
    interval_months: int = Field(default=1, ge=1, le=12, description="1 — каждый месяц, 12 — раз в год.")
    category: str | None = None
    remind_days: int = Field(default=2, ge=0, le=14, description="За сколько дней напомнить.")


def finance_tools() -> list[Tool]:
    async def add(a: AddArgs, ctx: ToolContext) -> ToolOutcome:
        cats = await fs.categories(ctx.session)
        cat = await fs.find_category(ctx.session, a.category, a.kind) if a.category else None
        cat = cat or fs.match_category(a.note, cats, a.kind) or fs.default_category(cats, a.kind)
        t = await fs.add_transaction(ctx.session, amount=_kop(a.amount), kind=a.kind, day=_date(a.date, ctx), category=cat, note=a.note)
        alerts = await fs.budget_alerts(ctx.session, t)
        sign = "+" if t.kind == "income" else "−"
        out = ToolOutcome(
            {"saved": fs.tx_out(t, {c.id: c for c in cats}), "budget_alerts": alerts},
            card=_card(f"{sign}{fs.rub(t.amount)} · {cat.name if cat else 'без категории'} · {t.note or ''}".rstrip(" ·")),
        )
        return out

    async def report(a: MonthArgs, ctx: ToolContext) -> ToolOutcome:
        ym = a.month or f"{_today(ctx):%Y-%m}"
        sm = await fs.summary(ctx.session, ym, _today(ctx))
        readable = {
            "month": ym,
            "доходы": fs.rub(sm["income"]),
            "расходы": fs.rub(sm["expense"]),
            "бюджет": fs.rub(sm["budget"]) if sm["budget"] else "не задан (лимиты категорий = 0)",
            "осталось": fs.rub(sm["remaining"]) if sm["budget"] else None,
            "можно_в_день": fs.rub(sm["per_day"]) if sm["per_day"] else None,
            "категории": [
                {"name": c["name"], "потрачено": fs.rub(c["spent"]), "лимит": fs.rub(c["limit"]) if c["limit"] else None}
                for c in sm["categories"]
            ],
            "ближайшие_платежи": [{"id": r["id"], "title": r["title"], "сумма": r["amount_text"], "дата": r["next_due"]} for r in sm["upcoming"]],
        }
        return ToolOutcome(readable)

    async def list_(a: ListArgs, ctx: ToolContext) -> ToolOutcome:
        start, end = fs.month_bounds(a.month or f"{_today(ctx):%Y-%m}")
        cat = await fs.find_category(ctx.session, a.category) if a.category else None
        cats = {c.id: c for c in await fs.categories(ctx.session, include_archived=True)}
        rows = await fs.transactions(ctx.session, start, end, cat.id if cat else None, limit=60)
        return ToolOutcome({"transactions": [fs.tx_out(t, cats) for t in rows]})

    async def delete(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
        from app.db.models import FinTransaction

        t = await ctx.session.get(FinTransaction, a.id)
        if t is None:
            raise fs.FinanceError(f"Нет операции #{a.id}.")
        await ctx.session.delete(t)
        return ToolOutcome({"deleted": a.id}, card=_card(f"удалена операция {fs.rub(t.amount)} · {t.note}"))

    async def limit(a: LimitArgs, ctx: ToolContext) -> ToolOutcome:
        cat = await fs.find_category(ctx.session, a.category, "expense")
        if cat is None:
            raise fs.FinanceError(f"Не нашёл категорию «{a.category}».")
        cat.monthly_limit = a.monthly_limit
        return ToolOutcome({"category": fs.category_out(cat)}, card=_card(f"лимит «{cat.name}»: {fs.rub(a.monthly_limit * 100)} в месяц"))

    async def recurring(a: RecurringArgs, ctx: ToolContext) -> ToolOutcome:
        from app.db.models import FinRecurring

        cat = await fs.find_category(ctx.session, a.category or a.title, "expense")
        r = FinRecurring(
            title=a.title.strip(), amount=_kop(a.amount), category_id=cat.id if cat else None, day_of_month=a.day_of_month,
            interval_months=a.interval_months, next_due=fs.first_due(_today(ctx), a.day_of_month), remind_days=a.remind_days,
        )  # fmt: skip
        ctx.session.add(r)
        await ctx.session.flush()
        return ToolOutcome(
            {"recurring": fs.recurring_out(r, {cat.id: cat} if cat else {})},
            card=_card(f"регулярный платёж: {r.title} {fs.rub(r.amount)}, ближайший {r.next_due:%d.%m}"),
        )

    async def paid(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
        r, t = await fs.mark_paid(ctx.session, a.id, _today(ctx))
        return ToolOutcome(
            {"paid": t.id, "next_due": r.next_due.isoformat()},
            card=_card(f"оплачено: {r.title} {fs.rub(t.amount)}, следующий {r.next_due:%d.%m}"),
        )

    return [
        Tool(
            "finance_add",
            "Записать расход или доход. Пользователь пишет коротко: «кофе 350», «такси 640 вчера», «зарплата 150 000». "
            "Сумма в рублях; категорию подбери по смыслу из существующих (см. finance_report).",
            AddArgs,
            add,
            "Записываю…",
        ),
        Tool("finance_report", "Сводка за месяц: доходы, расходы, бюджет, категории, ближайшие платежи.", MonthArgs, report, "Считаю…"),
        Tool("finance_list", "Список операций за месяц (с id), можно по категории.", ListArgs, list_, "Смотрю операции…"),
        Tool("finance_delete", "Удалить операцию по id (только по просьбе).", IdArgs, delete, "Удаляю операцию…"),
        Tool("finance_set_limit", "Задать месячный лимит категории расходов в рублях.", LimitArgs, limit, "Ставлю лимит…"),
        Tool(
            "finance_recurring_add",
            "Добавить регулярный платёж (аренда, подписка, кредит) с напоминанием заранее.",
            RecurringArgs,
            recurring,
            "Добавляю платёж…",
        ),
        Tool(
            "finance_recurring_paid",
            "Отметить регулярный платёж оплаченным (id из finance_report): запишется расход, срок сдвинется.",
            IdArgs,
            paid,
            "Отмечаю оплату…",
        ),
    ]
