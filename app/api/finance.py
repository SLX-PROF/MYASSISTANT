"""Finance planner API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import FinCategory, FinRecurring, FinTransaction, utcnow
from app.finance import service as fs
from app.security import require_session

router = APIRouter(prefix="/api/finance", tags=["finance"], dependencies=[Depends(require_session)])


def _today(request: Request) -> date:
    return utcnow().astimezone(request.app.state.settings.tz).date()


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/summary")
async def summary(request: Request, month: str | None = None):
    today = _today(request)
    try:
        async with request.app.state.db.session() as s:
            return await fs.summary(s, month or f"{today:%Y-%m}", today)
    except fs.FinanceError as e:
        raise _bad(e) from e


@router.get("/transactions")
async def list_transactions(request: Request, month: str | None = None, category_id: int | None = None):
    try:
        start, end = fs.month_bounds(month or f"{_today(request):%Y-%m}")
    except fs.FinanceError as e:
        raise _bad(e) from e
    async with request.app.state.db.session() as s:
        cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
        return [fs.tx_out(t, cats) for t in await fs.transactions(s, start, end, category_id)]


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


@router.post("/parse")
async def parse(body: ParseIn, request: Request):
    """Preview of a quick entry («такси 640 вчера») before saving."""
    try:
        async with request.app.state.db.session() as s:
            p = await fs.parse_quick(s, body.text, _today(request))
    except fs.FinanceError as e:
        raise _bad(e) from e
    return {
        "amount": p.amount, "amount_text": fs.rub(p.amount), "kind": p.kind, "day": p.day.isoformat(), "note": p.note,
        "category_id": p.category.id if p.category else None, "category": p.category.name if p.category else None,
    }  # fmt: skip


class TxIn(BaseModel):
    amount: int = Field(gt=0, le=10_000_000_000, description="kopecks")
    kind: Literal["expense", "income"] = "expense"
    day: date
    category_id: int | None = None
    note: str = Field(default="", max_length=200)


@router.post("/transactions")
async def create_transaction(body: TxIn, request: Request):
    st = request.app.state
    async with st.db.session() as s:
        cat = await s.get(FinCategory, body.category_id) if body.category_id else None
        try:
            t = await fs.add_transaction(s, amount=body.amount, kind=body.kind, day=body.day, category=cat, note=body.note)
        except fs.FinanceError as e:
            raise _bad(e) from e
        alerts = await fs.budget_alerts(s, t)
        await s.commit()
        cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
        out = fs.tx_out(t, cats)
    for text in alerts:
        await st.monitor.messenger.send(text, title="Бюджет")
    return {**out, "alerts": alerts}


@router.delete("/transactions/{tx_id}")
async def delete_transaction(tx_id: int, request: Request):
    async with request.app.state.db.session() as s:
        t = await s.get(FinTransaction, tx_id)
        if t is None:
            raise HTTPException(404, "Нет такой операции")
        await s.delete(t)
        await s.commit()
    return {"ok": True}


@router.get("/categories")
async def list_categories(request: Request):
    async with request.app.state.db.session() as s:
        return [fs.category_out(c) for c in await fs.categories(s)]


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: Literal["expense", "income"] = "expense"
    monthly_limit: int = Field(default=0, ge=0, le=100_000_000)
    color: str = Field(default="#94a3b8", pattern=r"^#[0-9a-fA-F]{6}$")
    keywords: str = Field(default="", max_length=1000)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    monthly_limit: int | None = Field(default=None, ge=0, le=100_000_000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    keywords: str | None = Field(default=None, max_length=1000)
    archived: bool | None = None


@router.post("/categories")
async def create_category(body: CategoryIn, request: Request):
    async with request.app.state.db.session() as s:
        if await s.scalar(select(FinCategory).where(FinCategory.name == body.name.strip())):
            raise HTTPException(400, "Такая категория уже есть.")
        c = FinCategory(**{**body.model_dump(), "name": body.name.strip()}, position=100)
        s.add(c)
        await s.commit()
        return fs.category_out(c)


@router.patch("/categories/{cat_id}")
async def patch_category(cat_id: int, body: CategoryPatch, request: Request):
    async with request.app.state.db.session() as s:
        c = await s.get(FinCategory, cat_id)
        if c is None:
            raise HTTPException(404, "Нет такой категории")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(c, k, v.strip() if isinstance(v, str) else v)
        await s.commit()
        return fs.category_out(c)


@router.get("/recurring")
async def list_recurring(request: Request):
    async with request.app.state.db.session() as s:
        cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
        rows = await s.scalars(select(FinRecurring).order_by(FinRecurring.active.desc(), FinRecurring.next_due))
        return [fs.recurring_out(r, cats) for r in rows]


class RecurringIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    amount: int = Field(gt=0, le=10_000_000_000, description="kopecks")
    category_id: int | None = None
    day_of_month: int = Field(ge=1, le=31)
    interval_months: int = Field(default=1, ge=1, le=12)
    remind_days: int = Field(default=2, ge=0, le=14)


class RecurringPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    amount: int | None = Field(default=None, gt=0, le=10_000_000_000)
    category_id: int | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    interval_months: int | None = Field(default=None, ge=1, le=12)
    remind_days: int | None = Field(default=None, ge=0, le=14)
    next_due: date | None = None
    active: bool | None = None


@router.post("/recurring")
async def create_recurring(body: RecurringIn, request: Request):
    async with request.app.state.db.session() as s:
        r = FinRecurring(**body.model_dump(), next_due=fs.first_due(_today(request), body.day_of_month))
        s.add(r)
        await s.commit()
        cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
        return fs.recurring_out(r, cats)


@router.patch("/recurring/{rid}")
async def patch_recurring(rid: int, body: RecurringPatch, request: Request):
    async with request.app.state.db.session() as s:
        r = await s.get(FinRecurring, rid)
        if r is None:
            raise HTTPException(404, "Нет такого платежа")
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(r, k, v.strip() if isinstance(v, str) else v)
        if "day_of_month" in data and "next_due" not in data:
            r.next_due = fs.first_due(_today(request), r.day_of_month)
        r.reminded_for = None
        await s.commit()
        cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
        return fs.recurring_out(r, cats)


@router.delete("/recurring/{rid}")
async def delete_recurring(rid: int, request: Request):
    async with request.app.state.db.session() as s:
        r = await s.get(FinRecurring, rid)
        if r is None:
            raise HTTPException(404, "Нет такого платежа")
        await s.delete(r)
        await s.commit()
    return {"ok": True}


@router.post("/recurring/{rid}/paid")
async def recurring_paid(rid: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            r, t = await fs.mark_paid(s, rid, _today(request))
            await s.commit()
            cats = {c.id: c for c in await fs.categories(s, include_archived=True)}
            return {"recurring": fs.recurring_out(r, cats), "transaction": fs.tx_out(t, cats)}
    except fs.FinanceError as e:
        raise _bad(e) from e
