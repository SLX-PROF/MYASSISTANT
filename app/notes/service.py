"""Notes («второй мозг»): saved thoughts and links with search by meaning.

Search is local and cheap: words are reduced to rough stems (Russian endings
vary a lot) and matched by prefix. The agent widens a question into several
phrasings («реклама» → «таргет», «продвижение»), which covers the meaning part.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Note

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG = re.compile(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)", re.IGNORECASE)
STOP = {
    "что", "как", "это", "для", "про", "или", "где", "когда", "его", "она", "они", "все", "мне", "меня", "был", "была",
    "the", "and", "for", "with", "this", "that", "сохранял", "сохранила", "сохранил", "заметк", "заметки", "заметка",
}  # fmt: skip
MAX_TEXT = 4000


class NoteError(ValueError):
    pass


def stem(word: str) -> str:
    w = word.casefold().replace("ё", "е")
    if len(w) > 5:
        return w[:5]
    return w[:-1] if len(w) >= 4 and not w.isdigit() else w


def stems(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text) if len(w) >= 3 and w.casefold() not in STOP}


def first_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,;:!?)»") if m else ""


async def fetch_title(url: str, client: httpx.AsyncClient | None = None) -> str:
    """Best effort page title for a saved link; empty on any problem."""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    own = client is None
    client = client or httpx.AsyncClient(timeout=6, follow_redirects=True, max_redirects=3, headers={"User-Agent": "Mozilla/5.0 Atlas"})
    try:
        async with client.stream("GET", url) as r:
            if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                return ""
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > 300_000:
                    break
        page = body.decode(r.encoding or "utf-8", errors="ignore")
        m = _OG.search(page) or _TITLE.search(page)
        return " ".join(html.unescape(m.group(1)).split())[:300] if m else ""
    except (httpx.HTTPError, UnicodeError, ValueError) as e:
        log.info("note title fetch failed: %s", e.__class__.__name__)
        return ""
    finally:
        if own:
            await client.aclose()


async def add_note(s: AsyncSession, *, text: str, url: str = "", title: str = "", tags: str = "", source: str = "chat") -> Note:
    text = (text or "").strip()[:MAX_TEXT]
    url = (url or first_url(text)).strip()[:1000]
    if url and not url.lower().startswith(("http://", "https://")):
        raise NoteError("Ссылка должна начинаться с http:// или https://")
    if not text and not url:
        raise NoteError("Пустая заметка.")
    tags = ", ".join(t.strip().lstrip("#") for t in tags.split(",") if t.strip())[:300]
    n = Note(text=text, url=url, title=title.strip()[:300], tags=tags, source=source)
    s.add(n)
    await s.flush()
    return n


def note_out(n: Note) -> dict:
    return {
        "id": n.id, "text": n.text, "url": n.url, "title": n.title, "tags": [t.strip() for t in n.tags.split(",") if t.strip()],
        "source": n.source, "created_at": n.created_at.isoformat(),
    }  # fmt: skip


def _haystack(n: Note) -> set[str]:
    return stems(" ".join((n.text, n.title, n.tags, n.url.replace("/", " ").replace(".", " "))))


def _matches(query: set[str], hay: set[str]) -> int:
    return sum(1 for q in query if any(h.startswith(q) or q.startswith(h) and len(h) >= 4 for h in hay))


async def search(s: AsyncSession, queries: list[str], limit: int = 10, scan: int = 5000) -> list[Note]:
    """Notes matching any of the phrasings, best first, newer first on ties."""
    qs = [stems(q) for q in queries if q.strip()]
    qs = [q for q in qs if q]
    rows = (await s.scalars(select(Note).order_by(Note.id.desc()).limit(scan))).all()
    if not qs:
        return list(rows[:limit])
    scored = []
    for n in rows:
        hay = _haystack(n)
        score = max((_matches(q, hay) / len(q) for q in qs), default=0)
        if score > 0:
            scored.append((score, n.id, n))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [n for _, _, n in scored[:limit]]


async def recent(s: AsyncSession, limit: int = 50, before_id: int | None = None) -> list[Note]:
    q = select(Note).order_by(Note.id.desc()).limit(limit)
    if before_id:
        q = q.where(Note.id < before_id)
    return list((await s.scalars(q)).all())


async def delete_note(s: AsyncSession, note_id: int) -> Note:
    n = await s.get(Note, note_id)
    if n is None:
        raise NoteError(f"Нет заметки #{note_id}.")
    await s.delete(n)
    return n


def short(n: Note, size: int = 80) -> str:
    base = n.title or n.text or n.url
    base = " ".join(base.split())
    return base if len(base) <= size else base[: size - 1] + "…"


def since(n: Note, now: datetime) -> int:
    return (now - n.created_at).days
