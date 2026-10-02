"""Agent tools for the content calendar (chat and Telegram)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import Field

from app.content import service as cs
from app.tools.builtin import _Args
from app.tools.registry import Tool, ToolContext, ToolOutcome

Rubric = Literal["beauty", "lifestyle", "office", "habits", "other"]
Stage = Literal["idea", "script", "filmed", "published"]
Platform = Literal["tiktok", "instagram", "youtube", "vk", "telegram", "pinterest"]


def _media(ctx: ToolContext):
    return ctx.settings.data_dir / "media" / "content"


def _today(ctx: ToolContext) -> date:
    return ctx.now.astimezone(ctx.tz).date()


def _card(text: str) -> dict:
    return {"type": "content", "text": text}


def _day(value: str | None) -> date | None:
    if not value or value == "bank":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise cs.ContentError("Дата в формате ГГГГ-ММ-ДД.") from None


class ListArgs(_Args):
    date_from: str | None = Field(default=None, description="YYYY-MM-DD; по умолчанию начало текущей недели.")
    date_to: str | None = Field(default=None, description="YYYY-MM-DD; по умолчанию +2 недели.")
    idea_bank: bool = Field(default=False, description="true — показать банк идей (без даты).")


class NewItem(_Args):
    date: str | None = Field(default=None, description="YYYY-MM-DD; пусто — в банк идей.")
    title: str = Field(min_length=1, max_length=200)
    rubric: Rubric = "other"
    icon: Literal[cs.ICONS] = "sparkles"  # type: ignore[valid-type]
    publish_time: str = Field(default="", description="ЧЧ:ММ или пусто.")
    platforms: list[Platform] = Field(default_factory=list, description="Где публикуем: tiktok, instagram, youtube, vk, telegram, pinterest.")
    hook: str = Field(default="", max_length=300, description="Хук на первые 2 секунды.")
    note: str = Field(default="", max_length=4000, description="Заметка или сценарий.")


class AddArgs(_Args):
    items: list[NewItem] = Field(min_length=1, max_length=31)


class UpdateArgs(_Args):
    id: int = Field(ge=1)
    date: str | None = Field(default=None, description="Перенести на YYYY-MM-DD; \"bank\" — убрать в банк идей.")
    title: str | None = Field(default=None, max_length=200)
    rubric: Rubric | None = None
    icon: Literal[cs.ICONS] | None = None  # type: ignore[valid-type]
    stage: Stage | None = Field(default=None, description="idea, script, filmed (снято), published.")
    publish_time: str | None = None
    platforms: list[Platform] | None = None
    hook: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=4000)


class IdArgs(_Args):
    id: int = Field(ge=1)


class LinkArgs(_Args):
    id: int = Field(ge=1)
    url: str = Field(max_length=1000)
    caption: str = Field(default="", max_length=300)


class MetaArgs(_Args):
    title: str | None = Field(default=None, max_length=60)
    goal: str | None = Field(default=None, max_length=60, description="Например «+5 000 подписчиков за месяц».")
    motto: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=6)
    week_of: str | None = Field(default=None, description="Любая дата недели YYYY-MM-DD для week_theme.")
    week_theme: str | None = Field(default=None, max_length=80, description="Тема недели; пусто — убрать.")


def content_tools() -> list[Tool]:
    async def list_(a: ListArgs, ctx: ToolContext) -> ToolOutcome:
        if a.idea_bank:
            items = await cs.list_items(ctx.session, None, None)
        else:
            start = _day(a.date_from) or cs.monday(_today(ctx))
            end = _day(a.date_to) or start + timedelta(days=13)
            items = await cs.list_items(ctx.session, start, end)
        out = await cs.items_out(ctx.session, items)
        return ToolOutcome({"items": out, "meta": await cs.get_meta(ctx.session)})

    async def add(a: AddArgs, ctx: ToolContext) -> ToolOutcome:
        made = []
        for it in a.items:
            item = await cs.add_item(
                ctx.session, _day(it.date), title=it.title, rubric=it.rubric, icon=it.icon,
                publish_time=it.publish_time, hook=it.hook, note=it.note, platforms=it.platforms,
            )  # fmt: skip
            made.append({"id": item.id, "day": item.day.isoformat() if item.day else None, "title": item.title})
        text = f"добавлено идей: {len(made)}" if len(made) > 1 else f"добавлено: {made[0]['title']}"
        return ToolOutcome({"created": made}, card=_card(text))

    async def update(a: UpdateArgs, ctx: ToolContext) -> ToolOutcome:
        fields = a.model_dump(exclude={"id", "date"}, exclude_none=True)
        move = "keep" if a.date is None else _day(a.date)
        item = await cs.update_item(ctx.session, a.id, move_to=move, **fields)
        where = item.day.strftime("%d.%m") if item.day else "банк идей"
        return ToolOutcome(
            {"updated": (await cs.items_out(ctx.session, [item]))[0]}, card=_card(f"{item.title} — {where}, {cs.STAGE_RU[item.stage]}")
        )

    async def delete(a: IdArgs, ctx: ToolContext) -> ToolOutcome:
        item = await cs.delete_item(ctx.session, a.id, _media(ctx))
        return ToolOutcome({"deleted": item.id}, card=_card(f"удалено: {item.title}"))

    async def link(a: LinkArgs, ctx: ToolContext) -> ToolOutcome:
        r = await cs.add_link(ctx.session, a.id, a.url, a.caption)
        return ToolOutcome({"ref": cs.ref_out(r)}, card=_card("референс прикреплён"))

    async def meta(a: MetaArgs, ctx: ToolContext) -> ToolOutcome:
        m = await cs.set_meta(
            ctx.session, title=a.title, goal=a.goal, motto=a.motto, tags=a.tags,
            week_of=_day(a.week_of) if a.week_of else None, week_theme=a.week_theme,
        )  # fmt: skip
        return ToolOutcome({"meta": m}, card=_card("шапка плана обновлена"))

    return [
        Tool(
            "content_list",
            "Показать контент-календарь (идеи роликов по дням) или банк идей. Используй перед правками, чтобы знать id.",
            ListArgs,
            list_,
            "Смотрю контент-план…",
        ),
        Tool(
            "content_add",
            "Добавить идеи роликов в контент-календарь (одну или сразу план на месяц). "
            "Если пользователь просит составить план — уточни нишу, площадку и цель, если неясно; "
            "идеи короткие и конкретные, 1–2 на день. Без даты — в банк идей.",
            AddArgs,
            add,
            "Добавляю в контент-план…",
        ),
        Tool(
            "content_update",
            "Изменить идею: перенести на другой день, сменить этап (снято, опубликовано), название, рубрику, "
            "время публикации, хук, заметку или сценарий.",
            UpdateArgs,
            update,
            "Обновляю контент-план…",
        ),
        Tool("content_delete", "Удалить идею из контент-плана. Только по явной просьбе.", IdArgs, delete, "Удаляю идею…"),
        Tool("content_add_link", "Прикрепить к идее ссылку-референс (TikTok, Pinterest и т. п.).", LinkArgs, link, "Прикрепляю референс…"),
        Tool(
            "content_set_meta",
            "Изменить шапку контент-плана: название, цель, девиз, теги или тему недели.",
            MetaArgs,
            meta,
            "Обновляю шапку плана…",
        ),
    ]
