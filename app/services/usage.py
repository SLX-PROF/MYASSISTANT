"""Claude API spend tracking and the monthly budget cap.

Costs are estimates from public per-token prices (USD per 1M tokens) and are
stored as integer micro-dollars. The provider console remains the source of
truth; this is the in-app guard rail.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LLMUsage, utcnow

# (input, output, cache read) USD per 1M tokens; cache writes cost 1.25x input.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-haiku-4": (1.0, 5.0, 0.10),
}
DEFAULT_PRICE = (4.0, 20.0, 0.20)  # unknown model: assume Opus-tier


class BudgetExceeded(Exception):
    def __init__(self, spent: float, budget: float):
        super().__init__(f"spent {spent:.2f} of {budget:.2f}")
        self.spent = spent
        self.budget = budget

    @property
    def user_message(self) -> str:
        return (
            f"Месячный лимит расхода на Claude исчерпан (${self.spent:.2f} из ${self.budget:.2f}). "
            "Увеличьте LLM_MONTHLY_BUDGET_USD или дождитесь следующего месяца."
        )


def price_for(model: str) -> tuple[float, float, float]:
    best = ""
    for prefix in PRICES:
        if model.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return PRICES[best] if best else DEFAULT_PRICE


def cost_micro_usd(model: str, usage: dict[str, int]) -> int:
    inp, out, cread = price_for(model)
    usd = (
        usage.get("input_tokens", 0) * inp
        + usage.get("output_tokens", 0) * out
        + usage.get("cache_read_input_tokens", 0) * cread
        + usage.get("cache_creation_input_tokens", 0) * inp * 1.25
    ) / 1_000_000
    return round(usd * 1_000_000)


def month_start(now: datetime | None = None) -> datetime:
    now = (now or utcnow()).astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def record(s: AsyncSession, *, purpose: str, model: str, usage: dict[str, int]) -> LLMUsage:
    row = LLMUsage(
        purpose=purpose,
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        cost_micro_usd=cost_micro_usd(model, usage),
    )
    s.add(row)
    await s.flush()
    return row


async def month_spend_usd(s: AsyncSession, now: datetime | None = None) -> float:
    total = await s.scalar(
        select(func.coalesce(func.sum(LLMUsage.cost_micro_usd), 0)).where(LLMUsage.created_at >= month_start(now))
    )
    return (total or 0) / 1_000_000


async def month_by_purpose(s: AsyncSession, now: datetime | None = None) -> dict[str, float]:
    rows = await s.execute(
        select(LLMUsage.purpose, func.sum(LLMUsage.cost_micro_usd))
        .where(LLMUsage.created_at >= month_start(now))
        .group_by(LLMUsage.purpose)
    )
    return {p: (c or 0) / 1_000_000 for p, c in rows.all()}


async def check_budget(s: AsyncSession, budget_usd: float) -> None:
    """Raise BudgetExceeded when the monthly cap is reached (0 = no cap)."""
    if budget_usd <= 0:
        return
    spent = await month_spend_usd(s)
    if spent >= budget_usd:
        raise BudgetExceeded(spent, budget_usd)
