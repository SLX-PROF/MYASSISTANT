"""Small persistent key/value state on top of the `settings` table."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting, utcnow


async def get(s: AsyncSession, key: str, default: Any = None) -> Any:
    row = await s.get(Setting, key)
    if row is None:
        return default
    return row.value.get("v", default)


async def put(s: AsyncSession, key: str, value: Any) -> None:
    row = await s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
        row.updated_at = utcnow()
