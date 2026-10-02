"""Personal finance: transactions, categories with monthly limits, regular payments.

Amounts are stored in kopecks (int). Everything is single-user.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FinCategory, FinGoal, FinRecurring, FinTransaction, utcnow
from app.services import kv


class FinanceError(ValueError):
    pass


DEFAULT_CATEGORIES = [
    # name, kind, color, keywords. Colors: a validated categorical palette
    # (neighbours stay distinguishable, also with color blindness).
    ("Продукты", "expense", "#c98500", "продукт,магазин,пятёрочк,пятерочк,перекрёст,перекрест,вкусвилл,ашан,лента,магнит,самокат,еда"),
    ("Кафе и рестораны", "expense", "#d95926", "кофе,кафе,ресторан,обед,ужин,завтрак,бар,доставк,шаурм,пицц,суши"),
    ("Транспорт", "expense", "#3987e5", "такси,метро,бензин,заправк,парковк,каршеринг,автобус,электричк,самолёт,самолет,поезд"),
    ("Дом и связь", "expense", "#9085e9", "аренд,квартир,жкх,коммунал,интернет,связь,телефон,мобильн"),
    ("Подписки и сервисы", "expense", "#d55181", "подписк,netflix,spotify,плюс,кинопоиск,icloud,claude,chatgpt,хостинг,сервер,vpn"),
    ("Здоровье", "expense", "#199e70", "аптек,врач,анализ,стоматолог,лекарств,клиник"),
    ("Красота и одежда", "expense", "#e66767", "одежд,обув,стрижк,маникюр,косметик,барбер"),
    ("Развлечения", "expense", "#008300", "кино,концерт,игр,театр,развлечен,музей"),
    ("Подарки", "expense", "#199e70", "подар,цвет"),
    ("Другое", "expense", "#94a3b8", ""),
    ("Зарплата", "income", "#3987e5", "зарплат,аванс,зп,премия"),
    ("Другие доходы", "income", "#199e70", "доход,кэшбэк,кешбэк,фриланс,продал,продаж,вернули,возврат"),
]

INCOME_WORDS = ("зарплат", "аванс", "доход", "получил", "пришл", "кэшбэк", "кешбэк", "премия", "вернули", "возврат", "продал")


def rub(kop: int) -> str:
    """12345600 -> '123 456 ₽'; kopecks shown only when present."""
    r, k = divmod(abs(kop), 100)
    s = f"{r:,}".replace(",", " ")
    return f"{s},{k:02d} ₽" if k else f"{s} ₽"


def month_bounds(ym: str) -> tuple[date, date]:
    try:
        y, m = (int(x) for x in ym.split("-"))
        start = date(y, m, 1)
    except ValueError:
        raise FinanceError("Месяц в формате ГГГГ-ММ, например 2026-10.") from None
    return start, date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, months: int, day_of_month: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(day_of_month, calendar.monthrange(y, m)[1]))


# ---------------------------------------------------------------- quick input

_NUM = re.compile(
    r"(?<![\w.])([+-]?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d{1,2})?|[+-]?\d+(?:[.,]\d{1,2})?)\s*(к|k|тыс\.?|т\.?)?(?:\s*(?:₽|р\.?|руб\.?|рублей|рубля))?(?![\w])",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")


@dataclass
class Parsed:
    amount: int  # kopecks
    kind: str
    category: FinCategory | None
    day: date
    note: str


def _parse_day(text: str, today: date) -> tuple[date, str]:
    low = text.lower()
    for word, delta in (("позавчера", 2), ("вчера", 1), ("сегодня", 0)):
        if word in low:
            return today - timedelta(days=delta), re.sub(word, "", text, flags=re.IGNORECASE)
    m = _DATE.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = today.year if not y else (int(y) + 2000 if len(y) == 2 else int(y))
        try:
            day = date(year, mo, d)
        except ValueError:
            raise FinanceError("Не понял дату.") from None
        if not y and day > today + timedelta(days=31):
            day = day.replace(year=year - 1)
        return day, text[: m.start()] + text[m.end() :]
    return today, text


def match_category(words: str, cats: list[FinCategory], kind: str | None) -> FinCategory | None:
    low = words.lower().replace("ё", "е")
    tokens = re.findall(r"[a-zа-я]+", low)
    for c in cats:
        if kind and c.kind != kind:
            continue
        if c.name.lower().replace("ё", "е") in low:
            return c
        for kw in filter(None, (k.strip().replace("ё", "е") for k in c.keywords.split(","))):
            if any(t.startswith(kw) for t in tokens):
                return c
    return None


async def parse_quick(s: AsyncSession, text: str, today: date) -> Parsed:
    """'такси 640 вчера', 'зарплата 150 000', '+5к кэшбэк', 'кофе 350 01.10'."""
    text = " ".join(text.split())[:200]
    day, rest = _parse_day(text, today)
    nums = list(_NUM.finditer(rest))
    if not nums:
        raise FinanceError("Не нашёл сумму. Пример: «такси 640 вчера».")
    m = nums[-1]
    raw = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    value = float(raw)
    if m.group(2):
        value *= 1000
    amount = round(abs(value) * 100)
    if amount <= 0 or amount > 100_000_000_00:
        raise FinanceError("Сумма должна быть больше нуля.")
    note = " ".join((rest[: m.start()] + " " + rest[m.end() :]).split()).strip(" ,.-+")
    low = note.lower()
    kind = "income" if raw.startswith("+") or any(w in low for w in INCOME_WORDS) else "expense"
    cats = await categories(s)
    # The same note as before keeps its category (learned from history).
    cat = None
    if note:
        # SQLite lower() does not handle Cyrillic, so compare in Python.
        recent = await s.execute(
            select(FinTransaction.note, FinTransaction.category_id)
            .where(FinTransaction.kind == kind)
            .order_by(FinTransaction.id.desc())
            .limit(500)
        )
        prev = next((cid for n, cid in recent.all() if n.casefold() == low.casefold()), None)
        cat = next((c for c in cats if c.id == prev), None)
    cat = cat or match_category(note, cats, kind) or default_category(cats, kind)
    if cat and cat.kind != kind:
        kind = cat.kind
    return Parsed(amount, kind, cat, day, note[:1].upper() + note[1:] if note else (cat.name if cat else ""))


def default_category(cats: list[FinCategory], kind: str) -> FinCategory | None:
    name = "Другое" if kind == "expense" else "Другие доходы"
    return next((c for c in cats if c.name == name), None) or next((c for c in cats if c.kind == kind), None)


# --------------------------------------------------------------- categories


async def ensure_defaults(s: AsyncSession) -> None:
    if await s.scalar(select(func.count()).select_from(FinCategory)):
        return
    for i, (name, kind, color, kw) in enumerate(DEFAULT_CATEGORIES):
        s.add(FinCategory(name=name, kind=kind, color=color, keywords=kw, position=i))
    await s.flush()


async def categories(s: AsyncSession, include_archived: bool = False) -> list[FinCategory]:
    q = select(FinCategory).order_by(FinCategory.position, FinCategory.id)
    if not include_archived:
        q = q.where(FinCategory.archived.is_(False))
    return list((await s.scalars(q)).all())


async def find_category(s: AsyncSession, name: str | None, kind: str | None = None) -> FinCategory | None:
    if not name:
        return None
    cats = await categories(s)
    low = name.strip().lower().replace("ё", "е")
    exact = next((c for c in cats if c.name.lower().replace("ё", "е") == low), None)
    return exact or match_category(name, cats, kind) or next((c for c in cats if low in c.name.lower()), None)


def category_out(c: FinCategory) -> dict:
    return {
        "id": c.id, "name": c.name, "kind": c.kind, "monthly_limit": c.monthly_limit, "color": c.color,
        "keywords": c.keywords, "archived": c.archived,
    }  # fmt: skip


# ------------------------------------------------------------- transactions


async def add_transaction(
    s: AsyncSession, *, amount: int, kind: str, day: date, category: FinCategory | None, note: str
) -> FinTransaction:
    if kind not in ("expense", "income"):
        raise FinanceError("Тип операции: expense или income.")
    if amount <= 0:
        raise FinanceError("Сумма должна быть больше нуля.")
    t = FinTransaction(amount=amount, kind=kind, day=day, category_id=category.id if category else None, note=note.strip()[:200])
    s.add(t)
    await s.flush()
    return t


def tx_out(t: FinTransaction, cats: dict[int, FinCategory]) -> dict:
    c = cats.get(t.category_id) if t.category_id else None
    return {
        "id": t.id, "day": t.day.isoformat(), "amount": t.amount, "amount_text": rub(t.amount), "kind": t.kind,
        "category_id": t.category_id, "category": c.name if c else "Без категории", "color": c.color if c else "#94a3b8",
        "note": t.note,
    }  # fmt: skip


async def transactions(s: AsyncSession, start: date, end: date, category_id: int | None = None, limit: int = 500) -> list[FinTransaction]:
    q = select(FinTransaction).where(FinTransaction.day >= start, FinTransaction.day <= end)
    if category_id:
        q = q.where(FinTransaction.category_id == category_id)
    return list((await s.scalars(q.order_by(FinTransaction.day.desc(), FinTransaction.id.desc()).limit(limit))).all())


async def spent_by_category(s: AsyncSession, start: date, end: date, kind: str = "expense") -> dict[int | None, int]:
    rows = await s.execute(
        select(FinTransaction.category_id, func.sum(FinTransaction.amount))
        .where(FinTransaction.day >= start, FinTransaction.day <= end, FinTransaction.kind == kind)
        .group_by(FinTransaction.category_id)
    )
    return {cid: int(total or 0) for cid, total in rows.all()}


def _share_rows(totals: dict[int | None, int], cats: list[FinCategory], kind: str) -> list[dict]:
    """Rows for the pie chart: every category with money in it, largest first."""
    by_id = {c.id: c for c in cats}
    rows = []
    for cid, v in totals.items():
        if not v:
            continue
        c = by_id.get(cid) if cid else None
        rows.append({"id": cid, "name": c.name if c else "Без категории", "color": c.color if c else "#94a3b8", "amount": v})
    rows.sort(key=lambda r: -r["amount"])
    return rows


async def history(s: AsyncSession, ym: str, months: int = 6) -> list[dict]:
    """Income and expense per month for the `months` months ending with `ym`."""
    start, _ = month_bounds(ym)
    first = add_months(start, -(months - 1), 1)
    rows = await s.execute(
        select(func.strftime("%Y-%m", FinTransaction.day), FinTransaction.kind, func.sum(FinTransaction.amount))
        .where(FinTransaction.day >= first, FinTransaction.day <= month_bounds(ym)[1])
        .group_by(func.strftime("%Y-%m", FinTransaction.day), FinTransaction.kind)
    )
    totals: dict[tuple[str, str], int] = {(m, k): int(v or 0) for m, k, v in rows.all()}
    out = []
    for i in range(months):
        m = f"{add_months(first, i, 1):%Y-%m}"
        inc, exp = totals.get((m, "income"), 0), totals.get((m, "expense"), 0)
        out.append({"month": m, "income": inc, "expense": exp, "net": inc - exp})
    return out


async def summary(s: AsyncSession, ym: str, today: date) -> dict:
    start, end = month_bounds(ym)
    cats = await categories(s)
    spent = await spent_by_category(s, start, end)
    income = int(
        await s.scalar(
            select(func.coalesce(func.sum(FinTransaction.amount), 0)).where(
                FinTransaction.day >= start, FinTransaction.day <= end, FinTransaction.kind == "income"
            )
        )
        or 0
    )
    expense = sum(spent.values())
    budget = sum(c.monthly_limit for c in cats if c.kind == "expense") * 100
    remaining = budget - expense if budget else 0
    days_left = (end - today).days + 1 if start <= today <= end else 0
    per_day = remaining // days_left if budget and days_left > 0 and remaining > 0 else 0
    cat_rows = []
    for c in cats:
        if c.kind != "expense":
            continue
        v = spent.get(c.id, 0)
        if v or c.monthly_limit:
            cat_rows.append({"id": c.id, "name": c.name, "color": c.color, "spent": v, "limit": c.monthly_limit * 100})
    if spent.get(None):
        cat_rows.append({"id": None, "name": "Без категории", "color": "#94a3b8", "spent": spent[None], "limit": 0})
    cat_rows.sort(key=lambda r: -r["spent"])
    income_by_cat = await spent_by_category(s, start, end, "income")
    upcoming = [
        recurring_out(r, {c.id: c for c in cats})
        for r in await s.scalars(
            select(FinRecurring)
            .where(FinRecurring.active.is_(True), FinRecurring.next_due <= today + timedelta(days=31))
            .order_by(FinRecurring.next_due)
        )
    ]
    return {
        "month": ym, "income": income, "expense": expense, "budget": budget, "remaining": remaining,
        "per_day": per_day, "days_left": days_left, "categories": cat_rows, "upcoming": upcoming,
        "expense_shares": _share_rows(spent, cats, "expense"), "income_shares": _share_rows(income_by_cat, cats, "income"),
        "goals": [goal_out(g, today) for g in await goals(s)],
    }  # fmt: skip


async def budget_alerts(s: AsyncSession, t: FinTransaction) -> list[str]:
    """Messages when a category crosses 80% / 100% of its limit (once per month and level)."""
    if t.kind != "expense" or not t.category_id:
        return []
    c = await s.get(FinCategory, t.category_id)
    if not c or not c.monthly_limit:
        return []
    ym = f"{t.day:%Y-%m}"
    start, end = month_bounds(ym)
    spent = (await spent_by_category(s, start, end)).get(c.id, 0)
    limit = c.monthly_limit * 100
    out = []
    for level, text in ((100, "Лимит превышен"), (80, "Потрачено 80% лимита")):
        if spent >= limit * level // 100:
            key = f"fin_alert:{ym}:{c.id}:{level}"
            if not await kv.get(s, key):
                await kv.put(s, key, True)
                out.append(f"{text}: {c.name} — {rub(spent)} из {rub(limit)} за {ym}.")
            break
    return out


# ---------------------------------------------------------------- recurring


def recurring_out(r: FinRecurring, cats: dict[int, FinCategory]) -> dict:
    c = cats.get(r.category_id) if r.category_id else None
    return {
        "id": r.id, "title": r.title, "amount": r.amount, "amount_text": rub(r.amount), "category_id": r.category_id,
        "category": c.name if c else "", "day_of_month": r.day_of_month, "interval_months": r.interval_months,
        "next_due": r.next_due.isoformat(), "remind_days": r.remind_days, "active": r.active,
    }  # fmt: skip


def first_due(today: date, day_of_month: int) -> date:
    this = add_months(today.replace(day=1), 0, day_of_month)
    return this if this >= today else add_months(today.replace(day=1), 1, day_of_month)


async def mark_paid(s: AsyncSession, rid: int, today: date) -> tuple[FinRecurring, FinTransaction]:
    r = await s.get(FinRecurring, rid)
    if r is None:
        raise FinanceError(f"Нет регулярного платежа #{rid}.")
    cat = await s.get(FinCategory, r.category_id) if r.category_id else None
    t = await add_transaction(s, amount=r.amount, kind="expense", day=today, category=cat, note=r.title)
    r.next_due = add_months(r.next_due, r.interval_months, r.day_of_month)
    r.reminded_for = None
    return r, t


async def due_reminders(s: AsyncSession, today: date) -> list[str]:
    """Payments whose reminder window has started; each due date is announced once."""
    out = []
    rows = await s.scalars(select(FinRecurring).where(FinRecurring.active.is_(True)).order_by(FinRecurring.next_due))
    for r in rows:
        if r.next_due - timedelta(days=r.remind_days) > today or r.reminded_for == r.next_due:
            continue
        r.reminded_for = r.next_due
        days = (r.next_due - today).days
        when = "сегодня" if days == 0 else "завтра" if days == 1 else f"через {days} дн." if days > 0 else f"просрочен на {-days} дн."
        out.append(
            f"Платёж {when}: {r.title} — {rub(r.amount)} ({r.next_due:%d.%m}).\n"
            f"Оплатили — напишите «{r.title.lower()} оплачен» или нажмите в разделе «Финансы»."
        )
    return out


# -------------------------------------------------------------------- goals


def months_between(today: date, deadline: date) -> int:
    """Whole months left to save, counting the current one (at least 1)."""
    return max(1, (deadline.year - today.year) * 12 + deadline.month - today.month + (1 if deadline.day >= today.day else 0))


def goal_out(g: FinGoal, today: date) -> dict:
    left = max(0, g.target - g.saved)
    per_month = 0
    if g.deadline and left and not g.done_at:
        per_month = -(-left // months_between(today, g.deadline)) if g.deadline >= today else left
    return {
        "id": g.id, "title": g.title, "target": g.target, "saved": g.saved, "left": left,
        "pct": round(min(g.saved / g.target, 1) * 100) if g.target else 0,
        "deadline": g.deadline.isoformat() if g.deadline else None, "color": g.color,
        "per_month": per_month, "overdue": bool(g.deadline and g.deadline < today and left), "done": bool(g.done_at),
    }  # fmt: skip


async def goals(s: AsyncSession, include_done: bool = True) -> list[FinGoal]:
    q = select(FinGoal).order_by(FinGoal.done_at.is_not(None), FinGoal.deadline.is_(None), FinGoal.deadline, FinGoal.id)
    if not include_done:
        q = q.where(FinGoal.done_at.is_(None))
    return list((await s.scalars(q)).all())


async def find_goal(s: AsyncSession, ref: str | int) -> FinGoal:
    if isinstance(ref, int) or str(ref).isdigit():
        g = await s.get(FinGoal, int(ref))
    else:
        low = str(ref).strip().casefold()
        rows = await goals(s)
        g = next((x for x in rows if x.title.casefold() == low), None) or next((x for x in rows if low in x.title.casefold()), None)
    if g is None:
        raise FinanceError(f"Нет цели «{ref}».")
    return g


async def add_goal(s: AsyncSession, *, title: str, target: int, deadline: date | None, saved: int = 0, color: str = "#5eead4") -> FinGoal:
    if target <= 0:
        raise FinanceError("Сумма цели должна быть больше нуля.")
    g = FinGoal(title=title.strip()[:100], target=target, saved=max(0, saved), deadline=deadline, color=color)
    if g.saved >= g.target:
        g.done_at = utcnow()
    s.add(g)
    await s.flush()
    return g


def deposit(g: FinGoal, amount: int) -> bool:
    """Add (or with a negative amount take back) money; returns True when the goal is reached now."""
    was_done = g.done_at is not None
    g.saved = max(0, g.saved + amount)
    if g.saved >= g.target and not was_done:
        g.done_at = utcnow()
        return True
    if g.saved < g.target:
        g.done_at = None
    return False


# ------------------------------------------------------------ weekly digest


async def weekly_message(s: AsyncSession, today: date) -> str | None:
    """Monday morning: how the past week (Mon-Sun) went, and what is coming."""
    week_end = today - timedelta(days=today.weekday() + 1)  # last Sunday
    week_start = week_end - timedelta(days=6)
    prev_start, prev_end = week_start - timedelta(days=7), week_start - timedelta(days=1)
    cats = await categories(s)
    by_id = {c.id: c for c in cats}
    spent = await spent_by_category(s, week_start, week_end)
    total = sum(spent.values())
    prev = sum((await spent_by_category(s, prev_start, prev_end)).values())
    income = sum((await spent_by_category(s, week_start, week_end, "income")).values())
    sm = await summary(s, f"{today:%Y-%m}", today)
    active_goals = [g for g in await goals(s, include_done=False)]
    if not total and not income and not sm["upcoming"] and not active_goals:
        return None
    lines = [f"Финансы за неделю {week_start:%d.%m}–{week_end:%d.%m}"]
    line = f"Расходы: {rub(total)}"
    if prev:
        diff = (total - prev) * 100 // prev
        line += f" ({'+' if diff > 0 else ''}{diff}% к прошлой неделе)" if diff else " (как на прошлой неделе)"
    lines.append(line)
    if income:
        lines.append(f"Доходы: {rub(income)}")
    top = sorted(((v, cid) for cid, v in spent.items() if v), reverse=True)[:3]
    if top:
        lines.append("Больше всего: " + ", ".join(f"{by_id[cid].name if cid in by_id else 'без категории'} {rub(v)}" for v, cid in top))
    over = [c for c in sm["categories"] if c["limit"] and c["spent"] > c["limit"]]
    if over:
        lines.append("Перерасход в месяце: " + ", ".join(f"{c['name']} (+{rub(c['spent'] - c['limit'])})" for c in over))
    if sm["budget"]:
        if sm["remaining"] > 0:
            lines.append(f"До конца месяца осталось {rub(sm['remaining'])}" + (f", это {rub(sm['per_day'])} в день" if sm["per_day"] else ""))
        else:
            lines.append(f"Бюджет месяца превышен на {rub(-sm['remaining'])}")
    soon = [r for r in sm["upcoming"] if date.fromisoformat(r["next_due"]) <= today + timedelta(days=6)]
    if soon:
        lines.append("Платежи на этой неделе: " + ", ".join(f"{r['title']} {r['amount_text']} ({date.fromisoformat(r['next_due']):%d.%m})" for r in soon))
    for g in active_goals[:3]:
        o = goal_out(g, today)
        tail = f", откладывать {rub(o['per_month'])} в месяц" if o["per_month"] else ""
        lines.append(f"Цель «{g.title}»: {o['pct']}% ({rub(g.saved)} из {rub(g.target)}){tail}")
    return "\n".join(lines)
