from datetime import date

import pytest

from app.core.agent import Agent
from app.core.llm.fake import FakeProvider, FakeTurn
from app.db.models import FinRecurring
from app.finance import service as fs
from app.finance.tools import finance_tools
from app.services.chat import create_conversation
from app.tools.builtin import build_registry

TODAY = date(2026, 10, 14)


@pytest.fixture
async def fdb(db):
    async with db.session() as s:
        await fs.ensure_defaults(s)
        await fs.ensure_defaults(s)  # idempotent
        await s.commit()
    return db


def test_money_and_months():
    assert fs.rub(15000000) == "150 000 ₽" and fs.rub(35050) == "350,50 ₽"
    assert fs.add_months(date(2026, 1, 31), 1, 31) == date(2026, 2, 28)
    assert fs.first_due(date(2026, 10, 14), 10) == date(2026, 11, 10)
    assert fs.first_due(date(2026, 10, 14), 20) == date(2026, 10, 20)
    with pytest.raises(fs.FinanceError):
        fs.month_bounds("октябрь")


@pytest.mark.parametrize(
    "text, amount, kind, cat, day, note",
    [
        ("такси 640 вчера", 64000, "expense", "Транспорт", date(2026, 10, 13), "Такси"),
        ("кофе 350", 35000, "expense", "Кафе и рестораны", TODAY, "Кофе"),
        ("зарплата 150 000", 15000000, "income", "Зарплата", TODAY, "Зарплата"),
        ("+5к кэшбэк", 500000, "income", "Другие доходы", TODAY, "Кэшбэк"),
        ("Пятёрочка 1 234,50 12.10", 123450, "expense", "Продукты", date(2026, 10, 12), "Пятёрочка"),
        ("что-то странное 99 руб", 9900, "expense", "Другое", TODAY, "Что-то странное"),
    ],
)
async def test_parse_quick(fdb, text, amount, kind, cat, day, note):
    async with fdb.session() as s:
        p = await fs.parse_quick(s, text, TODAY)
    assert (p.amount, p.kind, p.category.name, p.day, p.note) == (amount, kind, cat, day, note)


async def test_parse_errors_and_learning(fdb):
    async with fdb.session() as s:
        with pytest.raises(fs.FinanceError, match="сумму"):
            await fs.parse_quick(s, "просто текст", TODAY)
        # A note the user filed under another category is remembered.
        cats = {c.name: c for c in await fs.categories(s)}
        await fs.add_transaction(s, amount=100, kind="expense", day=TODAY, category=cats["Подарки"], note="Мармелад")
        p = await fs.parse_quick(s, "мармелад 200", TODAY)
        assert p.category.name == "Подарки"


async def test_summary_alerts_and_recurring(fdb):
    async with fdb.session() as s:
        cats = {c.name: c for c in await fs.categories(s)}
        cats["Кафе и рестораны"].monthly_limit = 1000
        t = await fs.add_transaction(s, amount=85000, kind="expense", day=TODAY, category=cats["Кафе и рестораны"], note="Ужин")
        assert await fs.budget_alerts(s, t) == ["Потрачено 80% лимита: Кафе и рестораны — 850 ₽ из 1 000 ₽ за 2026-10."]
        assert await fs.budget_alerts(s, t) == []  # once
        t2 = await fs.add_transaction(s, amount=20000, kind="expense", day=TODAY, category=cats["Кафе и рестораны"], note="Кофе")
        assert (await fs.budget_alerts(s, t2))[0].startswith("Лимит превышен")
        await fs.add_transaction(s, amount=15000000, kind="income", day=TODAY, category=cats["Зарплата"], note="Зарплата")

        s.add(FinRecurring(title="Аренда", amount=4500000, day_of_month=16, next_due=date(2026, 10, 16), remind_days=2))
        await s.flush()
        sm = await fs.summary(s, "2026-10", TODAY)
        assert sm["income"] == 15000000 and sm["expense"] == 105000 and sm["budget"] == 100000
        assert sm["remaining"] == -5000 and sm["per_day"] == 0
        assert sm["categories"][0]["name"] == "Кафе и рестораны" and sm["upcoming"][0]["title"] == "Аренда"

        msgs = await fs.due_reminders(s, TODAY)
        assert msgs and msgs[0].startswith("Платёж через 2 дн.: Аренда — 45 000 ₽")
        assert await fs.due_reminders(s, TODAY) == []  # announced once per due date
        r, tx = await fs.mark_paid(s, sm["upcoming"][0]["id"], TODAY)
        assert r.next_due == date(2026, 11, 16) and tx.amount == 4500000


async def test_agent_records_expense(fdb, settings):
    provider = FakeProvider(
        script=[
            FakeTurn(tool_calls=[("finance_add", {"amount": 640, "note": "такси", "date": "2026-10-13"})]),
            FakeTurn(text="Записал."),
        ]
    )
    agent = Agent(fdb, settings, provider, build_registry(finance_tools()))
    async with fdb.session() as s:
        cid = (await create_conversation(s)).id
        await s.commit()
    events = [e async for e in agent.run(cid, "такси 640 вчера")]
    card = next(e["card"] for e in events if e["type"] == "tool_result")
    assert card == {"type": "finance", "text": "−640 ₽ · Транспорт · такси"}


async def test_finance_api(authed):
    c = authed
    p = (await c.post("/api/finance/parse", json={"text": "кофе 350 вчера"})).json()
    assert p["amount"] == 35000 and p["category"] == "Кафе и рестораны"
    r = await c.post("/api/finance/transactions", json={"amount": p["amount"], "kind": p["kind"], "day": p["day"], "category_id": p["category_id"], "note": p["note"]})
    assert r.status_code == 200 and r.json()["amount_text"] == "350 ₽"
    cats = (await c.get("/api/finance/categories")).json()
    cafe = next(x for x in cats if x["name"] == "Кафе и рестораны")
    assert (await c.patch(f"/api/finance/categories/{cafe['id']}", json={"monthly_limit": 400})).json()["monthly_limit"] == 400
    month = p["day"][:7]
    sm = (await c.get(f"/api/finance/summary?month={month}")).json()
    assert sm["expense"] == 35000 and sm["budget"] == 40000
    rec = (await c.post("/api/finance/recurring", json={"title": "Сервер", "amount": 142500, "day_of_month": 5})).json()
    paid = (await c.post(f"/api/finance/recurring/{rec['id']}/paid")).json()
    assert paid["transaction"]["amount"] == 142500
    tx = (await c.get(f"/api/finance/transactions?month={month}")).json()
    assert any(t["note"] == "Сервер" for t in tx)
    assert (await c.delete(f"/api/finance/transactions/{tx[0]['id']}")).status_code == 200
    assert (await c.post("/api/finance/parse", json={"text": "без суммы"})).status_code == 400
