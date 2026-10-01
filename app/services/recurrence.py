"""Recurrence rules for reminders.

A rule is stored as a plain dict so it serialises into the DB as JSON:

    {"kind": "none"}
    {"kind": "daily",   "time": "10:00", "tz": "Europe/Moscow"}
    {"kind": "weekly",  "time": "10:00", "tz": "...", "weekdays": [0, 2, 4]}   # Mon=0
    {"kind": "monthly", "time": "10:00", "tz": "...", "anchor_day": 31}
    {"kind": "monthly", ..., "days": [1, 15]}                    # several days a month
    {"kind": "monthly", ..., "interval": 3, "anchor_month": 24324}  # every 3 months
    {"kind": "yearly",  "time": "10:00", "tz": "...", "month": 9, "day": 1}

Occurrences keep the *local wall-clock time* (10:00 stays 10:00 across DST
changes). Monthly rules clamp to the last day of short months without drifting.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_RU_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KINDS = {"none", "daily", "weekly", "monthly", "yearly"}
RU_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def build_rule(
    kind: str,
    first_fire: datetime,
    tz: ZoneInfo,
    weekdays: list[int] | None = None,
    interval: int = 1,
    days: list[int] | None = None,
) -> dict:
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
        if days:
            if any(d < 1 or d > 31 for d in days):
                raise ValueError("day of month out of range")
            rule["days"] = sorted(set(days))
        if interval < 1 or interval > 12:
            raise ValueError("interval must be 1..12 months")
        if interval > 1:
            rule["interval"] = interval
            rule["anchor_month"] = local.year * 12 + local.month - 1
    if kind == "yearly":
        rule["month"] = local.month
        rule["day"] = local.day
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
        days = rule.get("days") or [int(rule["anchor_day"])]
        interval = int(rule.get("interval", 1))
        anchor_month = int(rule.get("anchor_month", 0))
        y, m = start.year, start.month
        for _ in range(0, 12 * interval + 2):
            if (y * 12 + m - 1 - anchor_month) % interval == 0:
                last = calendar.monthrange(y, m)[1]
                for d in days:
                    cand = _local_dt(date(y, m, min(d, last)), at, tz)
                    if cand > after_local:
                        return cand.astimezone(timezone.utc)
            m += 1
            if m > 12:
                y, m = y + 1, 1

    if kind == "yearly":
        month, day = int(rule["month"]), int(rule["day"])
        for y in range(start.year, start.year + 3):
            cand = _local_dt(date(y, month, min(day, calendar.monthrange(y, month)[1])), at, tz)
            if cand > after_local:
                return cand.astimezone(timezone.utc)

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
        days = rule.get("days") or [rule.get("anchor_day")]
        day_txt = " и ".join(f"{d}-го" for d in days) + " числа"
        interval = int(rule.get("interval", 1))
        if interval == 1:
            return f"ежемесячно {day_txt} в {t}"
        if interval == 3:
            return f"раз в квартал, {day_txt} в {t}"
        return f"раз в {interval} мес., {day_txt} в {t}"
    if kind == "yearly":
        return f"каждый год {rule.get('day')} {RU_MONTHS_GEN[int(rule.get('month', 1)) - 1]} в {t}"
    return kind
