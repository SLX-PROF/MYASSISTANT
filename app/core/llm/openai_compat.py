"""Placeholder for OpenAI-compatible providers (OpenAI, local vLLM/Ollama, etc.).

TODO(stage N): implement `stream()`:
  * convert canonical history (Anthropic-style blocks, see base.py) to the
    chat.completions format: text -> content, tool_use -> tool_calls,
    tool_result -> role "tool" messages; drop reasoning/"thinking" blocks;
  * convert ToolSpec -> {"type": "function", "function": {...}};
  * stream deltas as TextDelta and finish with Completed(LLMResponse) whose
    `content` is converted back to canonical blocks.
Configure via LLM_PROVIDER=openai_compat plus base URL / key settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.llm.base import LLMError, LLMProvider, StreamEvent, ToolSpec


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, *_, **__):
        raise LLMError("Провайдер openai_compat ещё не реализован. Используйте LLM_PROVIDER=anthropic или fake.")

    async def stream(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], max_tokens: int | None = None
    ) -> AsyncIterator[StreamEvent]:  # pragma: no cover
        raise NotImplementedError
        yield  # makes this an async generator
