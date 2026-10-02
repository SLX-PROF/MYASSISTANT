"""Agent tools for notes («второй мозг»)."""

from __future__ import annotations

from pydantic import Field

from app.notes import service as ns
from app.tools.builtin import _Args
from app.tools.registry import Tool, ToolContext, ToolOutcome


def _card(text: str) -> dict:
    return {"type": "note", "text": text}


class SaveArgs(_Args):
    text: str = Field(default="", max_length=4000, description="Мысль или описание ссылки — как сказал пользователь.")
    url: str = Field(default="", max_length=1000, description="Ссылка, если есть.")
    tags: str = Field(default="", max_length=300, description="2–4 тега через запятую по смыслу: «реклама, идеи».")


class SearchArgs(_Args):
    queries: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Вопрос пользователя в 2–5 формулировках: синонимы и близкие слова (реклама → реклама, таргет, продвижение, маркетинг).",
    )


class IdArgs(_Args):
    id: int = Field(ge=1)


def notes_tools() -> list[Tool]:
    async def save(a: SaveArgs, ctx: ToolContext) -> ToolOutcome:
        n = await ns.add_note(ctx.session, text=a.text, url=a.url, tags=a.tags, source="chat")
        if n.url and not n.title:
            n.title = await ns.fetch_title(n.url)
        return ToolOutcome({"saved": ns.note_out(n)}, card=_card(f"сохранено: {ns.short(n)}"))

    async def search(a: SearchArgs, ctx: ToolContext) -> ToolOutcome:
        rows = await ns.search(ctx.session, a.queries, limit=10)
        return ToolOutcome({"notes": [ns.note_out(n) for n in rows], "found": len(rows)})

    async def delete(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
        n = await ns.delete_note(ctx.session, a.id)
        return ToolOutcome({"deleted": a.id}, card=_card(f"удалено: {ns.short(n)}"))

    return [
        Tool(
            "note_save",
            "Сохранить мысль, идею или ссылку в заметки («запомни идею…», «сохрани ссылку»). "
            "Для фактов о самом пользователе (имя, привычки) используй remember_fact, а не заметки.",
            SaveArgs,
            save,
            "Сохраняю заметку…",
        ),
        Tool(
            "note_search",
            "Найти в заметках по смыслу: «что я сохранял про рекламу?». Передай несколько формулировок. "
            "Ответь кратко: что нашлось, со ссылками; если ничего — так и скажи.",
            SearchArgs,
            search,
            "Ищу в заметках…",
        ),
        Tool("note_delete", "Удалить заметку по id (только по просьбе).", IdArgs, delete, "Удаляю заметку…"),
    ]
