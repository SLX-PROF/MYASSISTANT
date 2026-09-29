"""Parsing and formatting of date/time values exchanged with the model and UI."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

RU_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class TimeParseError(ValueError):
    pass


def parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse ISO 8601. A value without offset is interpreted in the user's timezone."""
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as e:
        raise TimeParseError(
            f"Не удалось разобрать время '{value}'. Нужен формат ISO 8601, например 2026-09-30T10:00:00+03:00."
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_due(value: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Parse a task due value: either a date (YYYY-MM-DD) or a datetime.

    Returns (utc datetime, has_time). Date-only values map to local midnight.
    """
    v = value.strip()
    if len(v) == 10:
        try:
            d = date.fromisoformat(v)
        except ValueError as e:
            raise TimeParseError(f"Не удалось разобрать дату '{value}'. Формат: YYYY-MM-DD.") from e
        return datetime.combine(d, time(0, 0)).replace(tzinfo=tz).astimezone(timezone.utc), False
    return parse_datetime(v, tz), True


def to_local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(tz).isoformat(timespec="minutes")


def to_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def describe_now(now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    offset = local.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}"
    return (
        f"{local.strftime('%Y-%m-%d %H:%M')} ({RU_WEEKDAYS[local.weekday()]}), "
        f"часовой пояс {tz.key} (UTC{offset})"
    )
