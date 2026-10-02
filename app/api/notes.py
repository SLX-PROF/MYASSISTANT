"""Notes API (web UI)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.notes import service as ns
from app.security import require_session

router = APIRouter(prefix="/api/notes", tags=["notes"], dependencies=[Depends(require_session)])


class NoteIn(BaseModel):
    text: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=1000)
    tags: str = Field(default="", max_length=300)


@router.get("")
async def list_notes(request: Request, q: str = "", before: int | None = None):
    async with request.app.state.db.session() as s:
        rows = await ns.search(s, [q], limit=50) if q.strip() else await ns.recent(s, 50, before)
        return [ns.note_out(n) for n in rows]


@router.post("")
async def create_note(body: NoteIn, request: Request):
    try:
        async with request.app.state.db.session() as s:
            n = await ns.add_note(s, text=body.text, url=body.url, tags=body.tags, source="web")
            if n.url:
                n.title = await ns.fetch_title(n.url)
            await s.commit()
            return ns.note_out(n)
    except ns.NoteError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/{note_id}")
async def delete_note(note_id: int, request: Request):
    try:
        async with request.app.state.db.session() as s:
            await ns.delete_note(s, note_id)
            await s.commit()
    except ns.NoteError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}
