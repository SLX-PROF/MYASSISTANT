"""Tool registry: schemas for the model, argument validation, execution.

The agent can only call tools registered here. There are deliberately no tools
for code execution, shell, file system or arbitrary network access.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.base import ToolSpec
from app.services.items import ItemError
from app.services.timeparse import TimeParseError

if TYPE_CHECKING:
    from app.config import Settings
    from app.scheduler import ReminderScheduler

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    session: AsyncSession
    settings: Settings
    now: datetime
    conversation_id: int | None = None
    scheduler: ReminderScheduler | None = None

    @property
    def tz(self):
        return self.settings.tz


@dataclass
class ToolOutcome:
    data: Any  # JSON-serialisable result for the model
    card: dict | None = None  # UI card
    is_error: bool = False
    # Called after the DB transaction commits (e.g. to (re)schedule a reminder).
    after_commit: list[Callable[[], Any]] = field(default_factory=list)


Handler = Callable[[Any, ToolContext], Awaitable[ToolOutcome]]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    label: str  # short Russian status shown in the UI, e.g. "Создаю напоминание…"

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, model_schema(self.args_model))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        # Sorted for a byte-stable request prefix (prompt caching).
        return [self._tools[n].spec() for n in sorted(self._tools)]

    def label(self, name: str) -> str:
        t = self._tools.get(name)
        return t.label if t else "Выполняю действие…"

    async def execute(self, name: str, raw_args: Any, ctx: ToolContext) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome({"error": f"Неизвестный инструмент '{name}'."}, is_error=True)
        if not isinstance(raw_args, dict):
            return ToolOutcome({"error": "Аргументы должны быть JSON-объектом."}, is_error=True)
        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError as e:
            return ToolOutcome({"error": "Неверные аргументы: " + format_validation_error(e)}, is_error=True)
        try:
            return await tool.handler(args, ctx)
        except (ItemError, TimeParseError, ValueError) as e:
            return ToolOutcome({"error": str(e)}, is_error=True)
        except Exception:  # noqa: BLE001 - never crash the agent loop on a tool bug
            log.exception("tool %s failed", name)
            return ToolOutcome({"error": "Внутренняя ошибка инструмента."}, is_error=True)


def format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "аргументы"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def model_schema(model: type[BaseModel]) -> dict:
    """JSON schema with $refs inlined and titles removed (compact for the model)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return resolve(copy.deepcopy(defs[ref]))
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "properties" and isinstance(v, dict):
                    # keys here are field names (a field may be called "title")
                    out[k] = {name: resolve(sub) for name, sub in v.items()}
                elif k != "title":
                    out[k] = resolve(v)
            return out
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    out = resolve(schema)
    out.setdefault("properties", {})
    return out
