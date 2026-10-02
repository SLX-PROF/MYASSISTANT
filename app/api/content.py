"""Content calendar API (web app and Telegram Mini App)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.content import service as cs
from app.security import require_session

router = APIRouter(prefix="/api/content", tags=["content"], dependencies=[Depends(require_session)])

Rubric = Literal["beauty", "lifestyle", "office", "habits", "other"]
Stage = Literal["idea", "script", "filmed", "published"]


def _media(request: Request):
    return request.app.state.settings.data_dir / "media" / "content"


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_content(request: Request, start: date | None = None, end: date | None = None, bank: bool = False):
    if not bank and (start is None or end is None):
        raise HTTPException(400, "Нужны start и end (YYYY-MM-DD) или bank=true.")
    if start and end and (end - start).days > 62:
        raise HTTPException(400, "Не больше двух месяцев за раз.")
    async with request.app.state.db.session() as s:
        items = await cs.list_items(s, None if bank else start, None if bank else end)
        return {"meta": await cs.get_meta(s), "items": await cs.items_out(s, items)}


class ItemIn(BaseModel):
    day: date | None = None
    title: str = Field(min_length=1, max_length=200)
    rubric: Rubric = "other"
    icon: str = "sparkles"
    stage: Stage = "idea"
    publish_time: str = ""
    hook: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=4000)


class ItemPatch(BaseModel):
    day: date | None = None
    to_bank: bool = False
    title: str | None = Field(default=None, max_length=200)
    rubric: Rubric | None = None
    icon: str | None = None
    stage: Stage | None = None
    publish_time: str | None = None
    hook: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=4000)


async def _one(s, item) -> dict:
    return (await cs.items_out(s, [item]))[0]


@router.post("/items")
async def create_item(body: ItemIn, request: Request):
    try:
        async with request.app.state.db.session() as s:
            item = await cs.add_item(s, body.day, **body.model_dump(exclude={"day"}))
            await s.commit()
            return await _one(s, item)
    except cs.ContentError as e:
        raise _bad(e) from e


@router.patch("/items/{item_id}")
async def patch_item(item_id: int, body: ItemPatch, request: Request):
    move = None if body.to_bank else (body.day if "day" in body.model_fields_set and body.day else "keep")
    try:
        async with request.app.state.db.session() as s:
            item = await cs.update_item(s, item_id, move_to=move, **body.model_dump(exclude={"day", "to_bank"}, exclude_none=True))
            await s.commit()
            return await _one(s, item)
    except cs.ContentError as e:
        raise _bad(e) from e


class DuplicateIn(BaseModel):
    day: date | None = None


@router.post("/items/{item_id}/duplicate")
async def duplicate(item_id: int, body: DuplicateIn, request: Request):
    try:
        async with request.app.state.db.session() as s:
            item = await cs.duplicate_item(s, item_id, body.day if body.day else "keep")
            await s.commit()
            return await _one(s, item)
    except cs.ContentError as e:
        raise _bad(e) from e


@router.delete("/items/{item_id}")
async def delete(item_id: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            await cs.delete_item(s, item_id, _media(request))
            await s.commit()
    except cs.ContentError as e:
        raise _bad(e) from e
    return {"ok": True}


class LinkIn(BaseModel):
    url: str = Field(max_length=1000)
    caption: str = Field(default="", max_length=300)


@router.post("/items/{item_id}/links")
async def add_link(item_id: int, body: LinkIn, request: Request):
    try:
        async with request.app.state.db.session() as s:
            r = await cs.add_link(s, item_id, body.url, body.caption)
            await s.commit()
            return cs.ref_out(r)
    except cs.ContentError as e:
        raise _bad(e) from e


@router.post("/items/{item_id}/photos")
async def add_photo(item_id: int, request: Request, caption: str = ""):
    if int(request.headers.get("content-length") or 0) > cs.MAX_PHOTO:
        raise HTTPException(413, "Фото должно быть не больше 10 МБ.")
    data = await request.body()
    try:
        async with request.app.state.db.session() as s:
            r = await cs.add_photo(s, item_id, data, _media(request), caption[:300])
            await s.commit()
            return cs.ref_out(r)
    except cs.ContentError as e:
        raise _bad(e) from e


@router.delete("/refs/{ref_id}")
async def delete_ref(ref_id: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            await cs.delete_ref(s, ref_id, _media(request))
            await s.commit()
    except cs.ContentError as e:
        raise _bad(e) from e
    return {"ok": True}


@router.get("/media/{name}")
async def media(name: str, request: Request):
    path = cs.media_path(_media(request), name)
    if path is None:
        raise HTTPException(404, "Не найдено")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


class MetaIn(BaseModel):
    title: str | None = Field(default=None, max_length=60)
    goal: str | None = Field(default=None, max_length=60)
    motto: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=6)
    week_of: date | None = None
    week_theme: str | None = Field(default=None, max_length=80)


@router.put("/meta")
async def put_meta(body: MetaIn, request: Request):
    async with request.app.state.db.session() as s:
        meta = await cs.set_meta(s, **body.model_dump())
        await s.commit()
        return meta


@router.get("/stats")
async def stats(request: Request, start: date, end: date):
    async with request.app.state.db.session() as s:
        return await cs.month_stats(s, start, end)
