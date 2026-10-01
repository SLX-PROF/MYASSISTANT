"""Claude API provider (official `anthropic` SDK)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import anthropic
from anthropic import AsyncAnthropic

from app.config import Settings
from app.core.llm.base import Completed, LLMError, LLMProvider, LLMResponse, StreamEvent, TextDelta, ToolSpec

log = logging.getLogger(__name__)

# Drop (instead of rejecting the request) any replayed reasoning block whose
# conversation prefix no longer matches, e.g. after the history window moved.
BETA_THINKING_BINDING = "thinking-binding-controls-2026-08-01"
# Server-side retry on a fallback model when the main model declines a request.
BETA_FALLBACK = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            raise LLMError("Не задан ANTHROPIC_API_KEY. Укажите ключ в .env или выберите LLM_PROVIDER=fake.")
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self.effort = settings.llm_effort
        self.fallback = settings.llm_refusal_fallback
        self.client = AsyncAnthropic(api_key=key, max_retries=2, timeout=180.0)

    def _request(self, system: str, messages: list[dict], tools: list[ToolSpec], max_tokens: int | None) -> dict:
        betas = [BETA_THINKING_BINDING]
        req: dict = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": messages,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "eager_input_streaming": True,
                }
                for t in tools
            ],
            "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "output_config": {"effort": self.effort},
            # Automatic prompt caching of the stable prefix (tools, system, history).
            "cache_control": {"type": "ephemeral"},
        }
        if not req["tools"]:
            del req["tools"]
        if self.fallback:
            betas.append(BETA_FALLBACK)
            req["fallbacks"] = "default"
        req["betas"] = betas
        return req

    async def stream(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], max_tokens: int | None = None
    ) -> AsyncIterator[StreamEvent]:
        req = self._request(system, messages, tools, max_tokens)
        yielded = False
        for attempt in range(2):
            try:
                async with self.client.beta.messages.stream(**req) as stream:
                    async for event in stream:
                        if event.type == "text":
                            yielded = True
                            yield TextDelta(event.text)
                    final = await stream.get_final_message()
                break
            except ValueError:
                # The SDK could not parse a streamed tool input at all; re-issue once
                # (only if nothing was shown yet, to avoid duplicated text in the UI).
                if attempt == 1 or yielded:
                    raise LLMError("Модель вернула повреждённый ответ. Попробуйте ещё раз.", retryable=True)
                log.warning("unparseable tool input from model, retrying")
            except anthropic.AuthenticationError as e:
                raise LLMError("Ключ Claude API недействителен. Проверьте ANTHROPIC_API_KEY.") from e
            except anthropic.PermissionDeniedError as e:
                raise LLMError("Нет доступа к модели. Проверьте ключ и модель LLM_MODEL.") from e
            except anthropic.NotFoundError as e:
                raise LLMError(f"Модель «{self.model}» не найдена. Проверьте LLM_MODEL.") from e
            except anthropic.RateLimitError as e:
                raise LLMError("Превышен лимит запросов к модели. Попробуйте через минуту.", retryable=True) from e
            except anthropic.BadRequestError as e:
                log.error("model rejected request: %s", getattr(e, "message", e))
                raise LLMError("Модель отклонила запрос (ошибка 400). Подробности в логах сервера.") from e
            except anthropic.APIStatusError as e:
                raise LLMError(f"Сервис модели ответил ошибкой {e.status_code}. Попробуйте позже.", retryable=True) from e
            except anthropic.APIConnectionError as e:
                raise LLMError("Нет связи с сервером модели. Проверьте интернет на сервере.", retryable=True) from e

        content = [b.model_dump(mode="json", exclude_none=True) for b in final.content]
        usage = {}
        if final.usage:
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                v = getattr(final.usage, k, None)
                if isinstance(v, int):
                    usage[k] = v
        yield Completed(
            LLMResponse(
                content=content,
                stop_reason=final.stop_reason or "end_turn",
                provider=self.name,
                model=final.model or self.model,
                usage=usage,
            )
        )

    async def aclose(self) -> None:
        await self.client.close()
