"""Provider-neutral interface for the language model ("the brain").

Conversation history is kept in one canonical shape (Anthropic-style content
blocks), because it is the most expressive: text, tool_use, tool_result and
opaque reasoning blocks. Other providers convert to/from it.

    {"role": "user" | "assistant", "content": [ {"type": "text", "text": ...}, ... ]}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class LLMResponse:
    # Blocks to store and replay verbatim in later requests.
    content: list[dict]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | ...
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(b["id"], b["name"], b.get("input"))
            for b in self.content
            if b.get("type") == "tool_use"
        ]


@dataclass
class TextDelta:
    text: str


@dataclass
class Completed:
    response: LLMResponse


StreamEvent = TextDelta | Completed


class LLMError(Exception):
    """Failure talking to the model. `user_message` is safe to show (Russian)."""

    def __init__(self, user_message: str, *, retryable: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.retryable = retryable


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def stream(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> AsyncIterator[StreamEvent]:
        """Stream one model turn: zero or more TextDelta, then exactly one Completed."""

    async def aclose(self) -> None:  # pragma: no cover - optional
        return None
