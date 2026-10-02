"""Content calendar: planned posts/videos by day, an idea bank, references.

Single-user. Photos are stored under DATA_DIR/media/content with random names.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ContentItem, ContentRef, utcnow
from app.services import kv

RUBRICS = ("beauty", "lifestyle", "office", "habits", "other")
RUBRIC_RU = {"beauty": "бьюти", "lifestyle": "лайфстайл", "office": "офис", "habits": "привычки", "other": "другое"}
STAGES = ("idea", "script", "filmed", "published")
PLATFORMS = ("tiktok", "instagram", "youtube", "vk", "telegram", "pinterest")
PLATFORM_RU = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "vk": "VK", "telegram": "Telegram", "pinterest": "Pinterest"}
STAGE_RU = {"idea": "идея", "script": "сценарий", "filmed": "снято", "published": "опубликовано"}
ICONS = (
    "sparkles", "heart", "camera", "coffee", "moon", "bag", "briefcase", "sun", "flower", "utensils",
    "shirt", "book", "dumbbell", "plane", "home", "gift", "star", "music", "brush", "smile",
)  # fmt: skip
META_KEY = "content_meta"
DEFAULT_META = {
    "title": "Контент-план",
    "tags": ["лайфстайл", "бьюти", "стиль", "офис"],
    "goal": "",
    "motto": "",
    "week_themes": {},  # monday ISO date -> theme
}
PHOTO_TYPES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png", b"GIF8": ".gif"}
MAX_PHOTO = 10 * 1024 * 1024
MAX_PER_DAY = 6
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ContentError(ValueError):
    pass


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def validate_fields(fields: dict) -> dict:
    out = {}
    if "title" in fields and fields["title"] is not None:
        t = " ".join(str(fields["title"]).split())[:200]
        if not t:
            raise ContentError("Название не может быть пустым.")
        out["title"] = t
    if fields.get("rubric") is not None:
        fields = {**fields, "rubric": fields["rubric"] or "other"}
        if fields["rubric"] not in RUBRICS:
            raise ContentError(f"Рубрика: {', '.join(RUBRICS)}.")
        out["rubric"] = fields["rubric"]
    if fields.get("icon") is not None:
        out["icon"] = fields["icon"] if fields["icon"] in ICONS else "sparkles"
    if fields.get("stage") is not None:
        if fields["stage"] not in STAGES:
            raise ContentError(f"Этап: {', '.join(STAGES)}.")
        out["stage"] = fields["stage"]
    if fields.get("publish_time") is not None:
        pt = fields["publish_time"].strip()
        if pt and not _TIME.match(pt):
            raise ContentError("Время публикации в формате ЧЧ:ММ.")
        out["publish_time"] = pt
    if fields.get("platforms") is not None:
        bad = [x for x in fields["platforms"] if x not in PLATFORMS]
        if bad:
            raise ContentError(f"Площадки: {', '.join(PLATFORMS)}.")
        out["platforms"] = ",".join(p for p in PLATFORMS if p in fields["platforms"])
    for key, limit in (("hook", 300), ("note", 4000)):
        if fields.get(key) is not None:
            out[key] = str(fields[key]).strip()[:limit]
    return out


async def _next_position(s: AsyncSession, day: date | None) -> int:
    q = select(func.coalesce(func.max(ContentItem.position), -1))
    q = q.where(ContentItem.day.is_(None)) if day is None else q.where(ContentItem.day == day)
    return int(await s.scalar(q)) + 1


async def _check_room(s: AsyncSession, day: date | None, exclude: int | None = None) -> None:
    if day is None:
        return
    q = select(func.count()).select_from(ContentItem).where(ContentItem.day == day)
    if exclude:
        q = q.where(ContentItem.id != exclude)
    if await s.scalar(q) >= MAX_PER_DAY:
        raise ContentError(f"На {day:%d.%m} уже {MAX_PER_DAY} идей — это максимум для одного дня.")


async def add_item(s: AsyncSession, day: date | None, **fields) -> ContentItem:
    data = validate_fields(fields)
    if "title" not in data:
        raise ContentError("Нужно название идеи.")
    await _check_room(s, day)
    data.setdefault("rubric", "other")
    item = ContentItem(day=day, position=await _next_position(s, day), **data)
    s.add(item)
    await s.flush()
    return item


async def get_item(s: AsyncSession, item_id: int) -> ContentItem:
    item = await s.get(ContentItem, item_id)
    if item is None:
        raise ContentError(f"Нет идеи #{item_id}.")
    return item


async def update_item(s: AsyncSession, item_id: int, *, move_to: date | None | str = "keep", **fields) -> ContentItem:
    """move_to: a date, None (back to the idea bank) or "keep"."""
    item = await get_item(s, item_id)
    for k, v in validate_fields(fields).items():
        setattr(item, k, v)
    if move_to != "keep" and move_to != item.day:
        await _check_room(s, move_to, exclude=item.id)
        item.day = move_to
        item.position = await _next_position(s, move_to)
    item.updated_at = utcnow()
    await s.flush()
    return item


async def duplicate_item(s: AsyncSession, item_id: int, day: date | None | str = "keep") -> ContentItem:
    src = await get_item(s, item_id)
    target = src.day if day == "keep" else day
    copy = await add_item(
        s, target, title=src.title, rubric=src.rubric, icon=src.icon, publish_time=src.publish_time, hook=src.hook, note=src.note,
        platforms=[p for p in src.platforms.split(",") if p],
    )
    for r in await refs_of(s, [src.id]):
        if r.kind == "link":
            s.add(ContentRef(item_id=copy.id, kind="link", url=r.url, caption=r.caption))
    await s.flush()
    return copy


async def delete_item(s: AsyncSession, item_id: int, media_dir: Path) -> ContentItem:
    item = await get_item(s, item_id)
    for r in await refs_of(s, [item.id]):
        _remove_file(media_dir, r.file)
    await s.delete(item)
    await s.flush()
    return item


async def list_items(s: AsyncSession, start: date | None, end: date | None) -> list[ContentItem]:
    """Items between two dates; start=end=None returns the idea bank."""
    q = select(ContentItem)
    if start is None and end is None:
        q = q.where(ContentItem.day.is_(None))
    else:
        q = q.where(ContentItem.day >= start, ContentItem.day <= end)
    return list((await s.scalars(q.order_by(ContentItem.day, ContentItem.position, ContentItem.id))).all())


async def refs_of(s: AsyncSession, item_ids: list[int]) -> list[ContentRef]:
    if not item_ids:
        return []
    return list((await s.scalars(select(ContentRef).where(ContentRef.item_id.in_(item_ids)).order_by(ContentRef.id))).all())


def ref_out(r: ContentRef) -> dict:
    return {
        "id": r.id, "kind": r.kind, "url": r.url if r.kind == "link" else f"/api/content/media/{r.file}",
        "caption": r.caption,
    }  # fmt: skip


def item_out(item: ContentItem, refs: list[ContentRef]) -> dict:
    return {
        "id": item.id, "day": item.day.isoformat() if item.day else None, "position": item.position,
        "title": item.title, "rubric": item.rubric, "icon": item.icon, "stage": item.stage,
        "publish_time": item.publish_time, "hook": item.hook, "note": item.note,
        "platforms": [p for p in (item.platforms or "").split(",") if p],
        "refs": [ref_out(r) for r in refs if r.item_id == item.id],
    }  # fmt: skip


async def items_out(s: AsyncSession, items: list[ContentItem]) -> list[dict]:
    refs = await refs_of(s, [i.id for i in items])
    return [item_out(i, refs) for i in items]


# --------------------------------------------------------------- references


def clean_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url, re.IGNORECASE) or len(url) > 1000:
        raise ContentError("Ссылка должна начинаться с http:// или https://.")
    return url


async def add_link(s: AsyncSession, item_id: int, url: str, caption: str = "") -> ContentRef:
    await get_item(s, item_id)
    r = ContentRef(item_id=item_id, kind="link", url=clean_url(url), caption=caption.strip()[:300])
    s.add(r)
    await s.flush()
    return r


def photo_ext(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for magic, ext in PHOTO_TYPES.items():
        if data.startswith(magic):
            return ext
    raise ContentError("Поддерживаются фото JPEG, PNG, WEBP и GIF.")


async def add_photo(s: AsyncSession, item_id: int, data: bytes, media_dir: Path, caption: str = "") -> ContentRef:
    await get_item(s, item_id)
    if not data or len(data) > MAX_PHOTO:
        raise ContentError("Фото должно быть не больше 10 МБ.")
    name = secrets.token_hex(16) + photo_ext(data)
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / name).write_bytes(data)
    r = ContentRef(item_id=item_id, kind="photo", file=name, caption=caption.strip()[:300])
    s.add(r)
    await s.flush()
    return r


async def delete_ref(s: AsyncSession, ref_id: int, media_dir: Path) -> None:
    r = await s.get(ContentRef, ref_id)
    if r is None:
        raise ContentError(f"Нет референса #{ref_id}.")
    _remove_file(media_dir, r.file)
    await s.delete(r)
    await s.flush()


def _remove_file(media_dir: Path, name: str) -> None:
    if name and re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|gif)", name):
        (media_dir / name).unlink(missing_ok=True)


def media_path(media_dir: Path, name: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|gif)", name):
        return None
    p = media_dir / name
    return p if p.is_file() else None


# --------------------------------------------------------------------- meta


async def get_meta(s: AsyncSession) -> dict:
    return {**DEFAULT_META, **(await kv.get(s, META_KEY, {}) or {})}


async def set_meta(s: AsyncSession, **fields) -> dict:
    meta = await get_meta(s)
    for key, limit in (("title", 60), ("goal", 60), ("motto", 80)):
        if fields.get(key) is not None:
            meta[key] = " ".join(str(fields[key]).split())[:limit]
    if fields.get("tags") is not None:
        meta["tags"] = [" ".join(t.split())[:24] for t in fields["tags"] if t and t.strip()][:6]
    if fields.get("week_of") is not None:
        themes = dict(meta.get("week_themes") or {})
        key = monday(fields["week_of"]).isoformat()
        text = " ".join(str(fields.get("week_theme") or "").split())[:80]
        if text:
            themes[key] = text
        else:
            themes.pop(key, None)
        meta["week_themes"] = themes
    await kv.put(s, META_KEY, meta)
    return meta


# --------------------------------------------------------------- reporting


async def today_message(s: AsyncSession, today: date) -> str | None:
    items = [i for i in await list_items(s, today, today) if i.stage != "published"]
    if not items:
        return None
    lines = []
    for i in items:
        when = f" · публикация {i.publish_time}" if i.publish_time else ""
        if i.platforms:
            when += " · " + ", ".join(PLATFORM_RU[p] for p in i.platforms.split(",") if p in PLATFORM_RU)
        lines.append(f"• {i.title} ({STAGE_RU[i.stage]}{when})")
    tomorrow = await list_items(s, today + timedelta(days=1), today + timedelta(days=1))
    tail = "\n\nЗавтра: " + "; ".join(i.title for i in tomorrow) if tomorrow else ""
    return "Сегодня по контент-плану:\n" + "\n".join(lines) + tail


async def publish_soon(s: AsyncSession, now_local: datetime, minutes: int) -> list[str]:
    """«Через 30 минут публикация» — once per item, day and time."""
    today = now_local.date()
    out = []
    for i in await list_items(s, today, today):
        if not i.publish_time or i.stage == "published":
            continue
        hh, mm = (int(x) for x in i.publish_time.split(":"))
        at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        left = (at - now_local).total_seconds() / 60
        if not (0 <= left <= minutes):
            continue
        key = f"content_pub:{i.id}:{today.isoformat()}:{i.publish_time}"
        if await kv.get(s, key):
            continue
        await kv.put(s, key, True)
        state = "" if i.stage == "filmed" else f" Сейчас этап: {STAGE_RU[i.stage]}."
        out.append(f"Через {max(1, round(left))} мин публикация: {i.title} ({i.publish_time}).{state}")
    return out


async def evening_message(s: AsyncSession, today: date) -> str | None:
    """Evening check: tomorrow's ideas that are not filmed yet."""
    tomorrow = today + timedelta(days=1)
    todo = [i for i in await list_items(s, tomorrow, tomorrow) if i.stage in ("idea", "script")]
    if not todo:
        return None
    lines = "\n".join(f"• {i.title}" + (f" — публикация {i.publish_time}" if i.publish_time else "") + f" ({STAGE_RU[i.stage]})" for i in todo)
    return f"Завтра по плану, ещё не снято:\n{lines}"


async def month_stats(s: AsyncSession, start: date, end: date) -> dict:
    items = await list_items(s, start, end)
    by_stage = {st: 0 for st in STAGES}
    by_rubric: dict[str, int] = {}
    for i in items:
        by_stage[i.stage] += 1
        by_rubric[i.rubric or "other"] = by_rubric.get(i.rubric or "other", 0) + 1
    days = {i.day for i in items}
    return {"total": len(items), "by_stage": by_stage, "by_rubric": by_rubric, "days_planned": len(days)}
