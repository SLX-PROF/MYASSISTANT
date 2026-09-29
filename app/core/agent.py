"""Agent loop: model -> tools -> model ... -> final answer.

Each step is committed separately, so a crash mid-turn leaves a consistent
history. The history sent to the model is append-only: every stored message is
replayed exactly as it was first sent/received.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.core.llm.base import Completed, LLMError, LLMProvider, LLMResponse, TextDelta
from app.core.prompts import context_block, system_prompt
from app.db.models import Conversation, Message, utcnow
from app.db.session import Database
from app.scheduler import ReminderScheduler
from app.services import items
from app.services.chat import DEFAULT_TITLE, message_out, title_from_text, touch
from app.tools.registry import ToolContext, ToolRegistry

log = logging.getLogger(__name__)

REASONING_BLOCKS = {"thinking", "redacted_thinking"}
MSG_REFUSAL = "Не могу помочь с этим запросом."
MSG_TOO_MANY_STEPS = "Я остановился: слишком много шагов подряд. Уточните, пожалуйста, что сделать."
MSG_TRUNCATED = "Ответ получился слишком длинным и был обрезан. Попросите продолжить или сформулируйте короче."


class ConversationNotFound(Exception):
    pass


class Agent:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        provider: LLMProvider,
        registry: ToolRegistry,
        scheduler: ReminderScheduler | None = None,
    ):
        self.db = db
        self.settings = settings
        self.provider = provider
        self.registry = registry
        self.scheduler = scheduler

    # ------------------------------------------------------------------ run

    async def run(self, conversation_id: int, user_text: str) -> AsyncIterator[dict]:
        """Process one user message; yields UI events (dicts)."""
        user_msg = await self._save_user_message(conversation_id, user_text)
        yield {"type": "user_message", "message": message_out(user_msg)}
        # Frozen for the whole turn so the request prefix stays stable.
        system = await self._system_prompt()

        for _ in range(self.settings.agent_max_iterations):
            history, start_id = await self._history(conversation_id)
            response: LLMResponse | None = None
            try:
                async for ev in self.provider.stream(
                    system=system, messages=history, tools=self.registry.specs()
                ):
                    if isinstance(ev, TextDelta):
                        yield {"type": "delta", "text": ev.text}
                    elif isinstance(ev, Completed):
                        response = ev.response
            except LLMError as e:
                log.warning("llm error: %s", e.user_message)
                yield {"type": "error", "message": e.user_message, "retryable": e.retryable}
                return
            if response is None:  # pragma: no cover - provider contract violation
                yield {"type": "error", "message": "Модель не вернула ответ."}
                return
            self._log_usage(response)

            calls = response.tool_calls
            stop = response.stop_reason
            display = response.text
            if stop == "refusal" and not display:
                display = MSG_REFUSAL
            if stop == "max_tokens":
                display = (display + "\n\n" if display else "") + MSG_TRUNCATED
            assistant_msg = await self._save_assistant(conversation_id, response, display, start_id)
            yield {"type": "assistant_message", "message": message_out(assistant_msg)}

            # Only run tools on a complete, non-refused turn.
            if not calls or stop in ("refusal", "max_tokens"):
                yield {"type": "done"}
                return

            tool_msg_events = []
            async for ev in self._run_tools(conversation_id, calls):
                if ev["type"] == "tool_message":
                    tool_msg_events.append(ev)
                else:
                    yield ev
            for ev in tool_msg_events:
                yield ev

        msg = await self._save_system_note(conversation_id, MSG_TOO_MANY_STEPS)
        yield {"type": "assistant_message", "message": message_out(msg)}
        yield {"type": "done"}

    # ---------------------------------------------------------------- tools

    async def _run_tools(self, conversation_id: int, calls) -> AsyncIterator[dict]:
        results: list[dict] = []
        cards: list[dict] = []
        after_commit = []
        async with self.db.session() as s:
            ctx = ToolContext(
                session=s,
                settings=self.settings,
                now=utcnow(),
                conversation_id=conversation_id,
                scheduler=self.scheduler,
            )
            for call in calls:
                yield {"type": "tool_start", "name": call.name, "label": self.registry.label(call.name)}
                if self.settings.log_verbose:
                    log.info("tool %s args=%s", call.name, call.input)
                else:
                    log.info("tool %s", call.name)
                outcome = await self.registry.execute(call.name, call.input, ctx)
                if outcome.is_error:
                    # Undo partial writes of a failed tool, keep earlier successful ones.
                    await s.rollback()
                else:
                    await s.commit()
                    after_commit.extend(outcome.after_commit)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(outcome.data, ensure_ascii=False, default=str),
                        **({"is_error": True} if outcome.is_error else {}),
                    }
                )
                if outcome.card and not outcome.is_error:
                    cards.append(outcome.card)
                yield {
                    "type": "tool_result",
                    "name": call.name,
                    "is_error": outcome.is_error,
                    "card": outcome.card if not outcome.is_error else None,
                    "error": outcome.data.get("error") if outcome.is_error and isinstance(outcome.data, dict) else None,
                }

            msg = Message(
                conversation_id=conversation_id,
                role="tool",
                text="",
                api_content=results,
                cards=cards or None,
            )
            s.add(msg)
            await s.commit()
        for fn in after_commit:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("after-commit hook failed")
        yield {"type": "tool_message", "message": message_out(msg)}

    # -------------------------------------------------------- persistence

    async def _system_prompt(self) -> str:
        async with self.db.session() as s:
            facts = await items.list_facts(s)
        facts_text = items.facts_block(facts, self.settings.facts_max_chars)
        return system_prompt(self.settings.assistant_name, facts_text)

    async def _save_user_message(self, conversation_id: int, text: str) -> Message:
        now = utcnow()
        async with self.db.session() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                raise ConversationNotFound(conversation_id)
            ctx = context_block(now, self.settings.tz)
            msg = Message(
                conversation_id=conversation_id,
                role="user",
                text=text,
                api_content=[{"type": "text", "text": ctx}, {"type": "text", "text": text}],
                created_at=now,
            )
            s.add(msg)
            if conv.title == DEFAULT_TITLE:
                conv.title = title_from_text(text)
            touch(conv)
            await s.commit()
            return msg

    async def _save_assistant(
        self, conversation_id: int, r: LLMResponse, display: str, start_id: int | None
    ) -> Message:
        async with self.db.session() as s:
            msg = Message(
                conversation_id=conversation_id,
                role="assistant",
                text=display,
                api_content=r.content or None,
                provider=r.provider,
                model=r.model,
                context_start_id=start_id,
            )
            s.add(msg)
            conv = await s.get(Conversation, conversation_id)
            if conv:
                touch(conv)
            await s.commit()
            return msg

    async def _save_system_note(self, conversation_id: int, text: str) -> Message:
        async with self.db.session() as s:
            msg = Message(
                conversation_id=conversation_id,
                role="assistant",
                text=text,
                api_content=[{"type": "text", "text": text}],
                provider="system",
            )
            s.add(msg)
            await s.commit()
            return msg

    # -------------------------------------------------------------- history

    async def _history(self, conversation_id: int) -> tuple[list[dict], int | None]:
        """History window for the model and the id of its first message.

        The window start moves in steps of half the limit (not one message at a
        time), so the prefix - and with it the prompt cache and replayable
        reasoning - stays stable between steps.
        """
        limit = self.settings.history_max_messages
        step = max(limit // 2, 1)
        base = (
            Message.conversation_id == conversation_id,
            Message.role.in_(("user", "assistant", "tool")),
            Message.api_content.is_not(None),
        )
        async with self.db.session() as s:
            total = await s.scalar(select(func.count()).select_from(Message).where(*base)) or 0
            offset = 0
            if total > limit:
                offset = -(-(total - limit) // step) * step  # ceil to a multiple of step
            rows = list(
                (await s.scalars(select(Message).where(*base).order_by(Message.id).offset(offset))).all()
            )
        return build_history(rows, self.provider.name)

    def _log_usage(self, r: LLMResponse) -> None:
        if r.usage:
            log.info("llm %s stop=%s usage=%s", r.model, r.stop_reason, r.usage)


def build_history(rows: list[Message], provider: str) -> tuple[list[dict], int | None]:
    """Canonical message list for the model, plus the id of its first message.

    * the window starts at a real user message;
    * consecutive same-role turns are merged (e.g. tool results + next user text);
    * reasoning blocks are replayed only to the provider that produced them and
      only while the window starts where it started when they were produced;
    * an assistant tool call left without results (crash mid-turn) gets an
      error result so the transcript stays valid.
    """
    first_user = next((i for i, m in enumerate(rows) if m.role == "user"), len(rows))
    rows = rows[first_user:]
    start_id = rows[0].id if rows else None

    out: list[dict] = []
    for i, m in enumerate(rows):
        role = "assistant" if m.role == "assistant" else "user"
        content: list[dict[str, Any]] = [dict(b) for b in (m.api_content or [])]
        if role == "assistant" and (m.provider != provider or m.context_start_id != start_id):
            content = [b for b in content if b.get("type") not in REASONING_BLOCKS]
        if not content:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(content)
        else:
            out.append({"role": role, "content": content})

        if role == "assistant":
            pending = [b["id"] for b in content if b.get("type") == "tool_use"]
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if pending and nxt is not None and nxt.role != "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": tid, "content": '{"error": "interrupted"}', "is_error": True}
                            for tid in pending
                        ],
                    }
                )
    return out, start_id
