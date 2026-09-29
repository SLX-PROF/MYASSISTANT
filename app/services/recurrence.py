"""Recurrence rules for reminders.

A rule is stored as a plain dict so it serialises into the DB as JSON:

    {"kind": "none"}
    {"kind": "daily",   "time": "10:00", "tz": "Europe/Moscow"}
    {"kind": "weekly",  "time": "10:00", "tz": "...", "weekdays": [0, 2, 4]}   # Mon=0
    {"kind": "monthly", "time": "10:00", "tz": "...", "anchor_day": 31}

Occurrences keep the *local wall-clock time* (10:00 stays 10:00 across DST
changes). Monthly rules clamp to the last day of short months without drifting.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_RU_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KINDS = {"none", "daily", "weekly", "monthly"}


def build_rule(kind: str, first_fire: datetime, tz: ZoneInfo, weekdays: list[int] | None = None) -> dict:
    """Create a rule anchored at the local time of the first occurrence."""
    if kind not in KINDS:
        raise ValueError(f"unknown recurrence kind: {kind}")
    if kind == "none":
        return {"kind": "none"}
    local = first_fire.astimezone(tz)
    rule: dict = {"kind": kind, "time": local.strftime("%H:%M"), "tz": tz.key}
    if kind == "weekly":
        days = sorted(set(weekdays or [local.weekday()]))
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("weekday out of range")
        rule["weekdays"] = days
    if kind == "monthly":
        rule["anchor_day"] = local.day
    return rule


def _local_dt(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t).replace(tzinfo=tz)


def next_occurrence(rule: dict, after: datetime) -> datetime | None:
    """First occurrence strictly after `after` (UTC result), or None for one-shot rules."""
    kind = rule.get("kind", "none")
    if kind == "none":
        return None
    tz = ZoneInfo(rule["tz"])
    hh, mm = (int(x) for x in rule["time"].split(":"))
    at = time(hh, mm)
    after_local = after.astimezone(tz)
    start = after_local.date()

    if kind == "daily":
        for offset in range(0, 3):
            cand = _local_dt(start + timedelta(days=offset), at, tz)
            if cand > after_local:
                return cand.astimezone(timezone.utc)

    if kind == "weekly":
        days = set(rule.get("weekdays") or [])
        if not days:
            return None
        for offset in range(0, 15):
            d = start + timedelta(days=offset)
            if d.weekday() in days:
                cand = _local_dt(d, at, tz)
                if cand > after_local:
                    return cand.astimezone(timezone.utc)

    if kind == "monthly":
        anchor = int(rule["anchor_day"])
        y, m = start.year, start.month
        for _ in range(0, 14):
            day = min(anchor, calendar.monthrange(y, m)[1])
            cand = _local_dt(date(y, m, day), at, tz)
            if cand > after_local:
                return cand.astimezone(timezone.utc)
            m += 1
            if m > 12:
                y, m = y + 1, 1

    raise RuntimeError(f"could not compute next occurrence for {rule}")


def describe(rule: dict) -> str:
    """Human-readable Russian label, e.g. 'каждый день в 10:00'."""
    kind = rule.get("kind", "none")
    if kind == "none":
        return "однократно"
    t = rule.get("time", "")
    if kind == "daily":
        return f"каждый день в {t}"
    if kind == "weekly":
        days = rule.get("weekdays") or []
        if sorted(days) == [0, 1, 2, 3, 4]:
            return f"по будням в {t}"
        if sorted(days) == [5, 6]:
            return f"по выходным в {t}"
        return f"еженедельно ({', '.join(WEEKDAY_RU_SHORT[d] for d in days)}) в {t}"
    if kind == "monthly":
        return f"ежемесячно {rule.get('anchor_day')}-го числа в {t}"
    return kind
