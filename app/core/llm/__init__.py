"""LLM provider factory."""

from __future__ import annotations

from app.config import Settings
from app.core.llm.base import LLMError, LLMProvider


def create_provider(settings: Settings) -> LLMProvider:
    name = settings.llm_provider.lower()
    if name == "anthropic":
        from app.core.llm.anthropic import AnthropicProvider

        return AnthropicProvider(settings)
    if name == "fake":
        from app.core.llm.fake import FakeProvider

        return FakeProvider(tz=settings.tz)
    if name == "openai_compat":
        from app.core.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(settings)
    raise LLMError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
