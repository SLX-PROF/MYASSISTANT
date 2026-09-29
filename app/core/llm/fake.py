"""Deterministic provider for tests and for running without an API key.

Two modes:
* scripted  - tests pass a list of turns; each call pops the next one.
* demo      - understands a few Russian commands so the app is usable without
              a key (e.g. "напомни через 2 минуты выпить воды").
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.llm.base import Completed, LLMProvider, LLMResponse, StreamEvent, TextDelta, ToolSpec

_ids = itertools.count(1)


@dataclass
class FakeTurn:
    text: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    stop_reason: str | None = None


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, script: list[FakeTurn] | None = None, tz: ZoneInfo | None = None):
        self.script = list(script) if script is not None else None
        self.tz = tz or ZoneInfo("Europe/Moscow")
        self.calls: list[dict] = []  # recorded requests (for tests)

    async def stream(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append({"system": system, "messages": messages, "tools": [t.name for t in tools]})
        turn = self._next_turn(messages)
        content: list[dict] = []
        if turn.text:
            for chunk in _chunks(turn.text):
                yield TextDelta(chunk)
            content.append({"type": "text", "text": turn.text})
        for name, args in turn.tool_calls:
            content.append({"type": "tool_use", "id": f"toolu_fake_{next(_ids)}", "name": name, "input": args})
        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end_turn")
        yield Completed(LLMResponse(content=content, stop_reason=stop, provider=self.name, model=self.model))

    # ------------------------------------------------------------------ logic

    def _next_turn(self, messages: list[dict]) -> FakeTurn:
        if self.script is not None:
            if not self.script:
                return FakeTurn(text="(сценарий закончился)")
            return self.script.pop(0)
        return self._demo(messages)

    def _demo(self, messages: list[dict]) -> FakeTurn:
        last = messages[-1] if messages else {"content": []}
        results = [b for b in last.get("content", []) if b.get("type") == "tool_result"]
        if results:
            return FakeTurn(text=_summarise(results))

        text = _user_text(last).strip()
        low = text.lower()
        now = datetime.now(timezone.utc).astimezone(self.tz)

        m = re.match(r"напомни\s+через\s+(\d+)\s*(сек\w*|мин\w*|час\w*)\s*(.*)", low)
        if m:
            n, unit, what = int(m.group(1)), m.group(2), m.group(3).strip() or "напоминание"
            delta = timedelta(seconds=n) if unit.startswith("сек") else (
                timedelta(hours=n) if unit.startswith("час") else timedelta(minutes=n)
            )
            when = (now + delta).isoformat(timespec="seconds")
            return FakeTurn(text="Создаю напоминание.", tool_calls=[("create_reminder", {"text": what, "when": when})])

        m = re.match(r"(?:добавь|создай)\s+задачу[:\s]+(.*)", low)
        if m:
            title = m.group(1).strip()
            args: dict = {"title": title}
            if "завтра" in title:
                args["title"] = re.sub(r"\s*(на\s+)?завтра\s*", " ", title).strip()
                args["due"] = (now + timedelta(days=1)).date().isoformat()
            return FakeTurn(tool_calls=[("create_task", args)])

        m = re.match(r"запомни[,:\s]+(?:что\s+)?(.*)", low)
        if m:
            return FakeTurn(tool_calls=[("remember_fact", {"text": m.group(1).strip()})])

        if re.search(r"\b(мои\s+)?задачи\b", low):
            return FakeTurn(tool_calls=[("list_tasks", {})])
        if re.search(r"напоминани", low):
            return FakeTurn(tool_calls=[("list_reminders", {})])
        if re.search(r"который час|сколько времени", low):
            return FakeTurn(tool_calls=[("get_current_time", {})])

        return FakeTurn(
            text=(
                "Я работаю в **демо-режиме** без языковой модели. Понимаю команды:\n\n"
                "- «напомни через 2 минуты выпить воды»\n"
                "- «добавь задачу купить продукты на завтра»\n"
                "- «запомни, что я пью кофе без сахара»\n"
                "- «мои задачи», «напоминания», «который час»\n\n"
                "Чтобы подключить Claude, задайте `ANTHROPIC_API_KEY` и `LLM_PROVIDER=anthropic` в `.env`."
            )
        )


def _user_text(message: dict) -> str:
    parts = [
        b.get("text", "")
        for b in message.get("content", [])
        if b.get("type") == "text" and not b.get("text", "").startswith("<context>")
    ]
    return "\n".join(parts)


def _summarise(results: list[dict]) -> str:
    if any(r.get("is_error") for r in results):
        try:
            err = json.loads(results[0]["content"]).get("error", "")
        except (ValueError, KeyError, TypeError):
            err = ""
        return f"Не получилось: {err}" if err else "Не получилось выполнить действие."
    return "Готово."


def _chunks(text: str, size: int = 12):
    for i in range(0, len(text), size):
        yield text[i : i + size]
