"""One-shot structured calls to the model for mail analysis.

No chat history, no other tools: the model gets a list of senders/letters as
data and must answer by calling the single `report` tool, so the result is
valid JSON. Every call is checked against the monthly budget and recorded
under purpose "mail" (visible in /cost).
"""

from __future__ import annotations

import json
import logging

from app.config import Settings
from app.core.llm.base import Completed, LLMError, LLMProvider, ToolSpec
from app.db.session import Database
from app.services import usage

log = logging.getLogger(__name__)


class MailLLMError(Exception):
    """Safe to show (Russian)."""


class MailLLM:
    def __init__(self, db: Database, settings: Settings, provider: LLMProvider):
        self.db = db
        self.settings = settings
        self.provider = provider

    @property
    def model(self) -> str:
        return self.provider.model

    async def report(self, system: str, data: str, task: str, schema: dict, max_tokens: int = 12000) -> dict:
        async with self.db.session() as s:
            try:
                await usage.check_budget(s, self.settings.llm_monthly_budget_usd)
            except usage.BudgetExceeded as e:
                raise MailLLMError(f"Месячный лимит Claude исчерпан (${e.spent:.2f} из ${e.budget:.2f}).") from e
        tool = ToolSpec("report", "Верни результат. Вызови ровно один раз.", schema)
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": f"<data>\n{data}\n</data>"}, {"type": "text", "text": task}],
            }
        ]
        response = None
        try:
            async for ev in self.provider.stream(system=system, messages=messages, tools=[tool], max_tokens=max_tokens):
                if isinstance(ev, Completed):
                    response = ev.response
        except LLMError as e:
            raise MailLLMError(e.user_message) from e
        if response is None:
            raise MailLLMError("Модель не ответила.")
        if response.usage:
            async with self.db.session() as s:
                await usage.record(s, purpose="mail", model=response.model, usage=response.usage)
                await s.commit()
        for call in response.tool_calls:
            if call.name == "report" and isinstance(call.input, dict):
                return call.input
        # Fallback: JSON in plain text.
        text = response.text
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                pass
        log.warning("mail analysis: no structured answer (stop=%s)", response.stop_reason)
        raise MailLLMError("Модель вернула ответ не в том формате. Попробуйте ещё раз.")


def estimate_usd(model: str, input_chars: int, calls: int, output_tokens: int) -> float:
    """Rough cost: Russian text is ~2.5 characters per token; plus a fixed prompt per call."""
    inp, out, _ = usage.price_for(model)
    input_tokens = input_chars / 2.5 + calls * 900
    thinking = calls * 600
    return (input_tokens * inp + (output_tokens + thinking) * out) / 1_000_000
