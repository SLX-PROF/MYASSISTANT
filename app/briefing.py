"""Morning brief: one Telegram message with the day ahead.

Personal only: today's tasks and reminders, payments soon, the month's
budget and the weather (Open-Meteo, free, no key; only the city coordinates
are sent).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

import httpx
from sqlalchemy import select

from app.config import Settings
from app.db.models import Reminder, Task
from app.db.session import Database
from app.finance import service as fs
from app.services.timeparse import RU_WEEKDAYS

log = logging.getLogger(__name__)

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# WMO weather codes (Open-Meteo) → Russian
WMO = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман с изморозью",
    51: "морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
}  # fmt: skip


def _deg(v: float) -> str:
    n = round(v)
    return f"+{n}°" if n > 0 else f"{n}°"


async def weather(settings: Settings, client: httpx.AsyncClient | None = None) -> str:
    """'+12°, пасмурно; днём до +15°, ночью +6°, осадки 40%' or '' on failure."""
    if settings.weather_lat is None or settings.weather_lon is None:
        return ""
    params = {
        "latitude": settings.weather_lat,
        "longitude": settings.weather_lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "timezone": settings.timezone,
        "forecast_days": 1,
    }
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        r.raise_for_status()
        d = r.json()
        cur, day = d["current"], d["daily"]
        text = f"{_deg(cur['temperature_2m'])}, {WMO.get(int(cur['weather_code']), 'без осадков')}"
        text += f"; днём до {_deg(day['temperature_2m_max'][0])}, ночью {_deg(day['temperature_2m_min'][0])}"
        rain = day.get("precipitation_probability_max", [None])[0]
        if rain:
            text += f", осадки {rain}%"
        return text
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("weather fetch failed: %s", e.__class__.__name__)
        return ""
    finally:
        if own:
            await client.aclose()


def _hhmm(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


async def morning_brief(db: Database, settings: Settings, now: datetime, weather_text: str = "") -> str:
    tz = settings.tz
    today = now.astimezone(tz).date()
    day_start = datetime.combine(today, time.min, tz)
    day_end = day_start + timedelta(days=1)
    lines = [f"Доброе утро! {RU_WEEKDAYS[today.weekday()].capitalize()}, {today.day} {MONTHS_GEN[today.month - 1]}"]
    if weather_text:
        lines.append(f"Погода ({settings.weather_city}): {weather_text}")
    async with db.session() as s:
        tasks = (
            await s.scalars(
                select(Task).where(Task.status == "open", Task.due_at.is_not(None), Task.due_at < day_end).order_by(Task.due_at)
            )
        ).all()
        reminders = (
            await s.scalars(
                select(Reminder)
                .where(Reminder.status == "active", Reminder.next_fire_at >= now, Reminder.next_fire_at < day_end)
                .order_by(Reminder.next_fire_at)
            )
        ).all()
        sm = await fs.summary(s, f"{today:%Y-%m}", today)
    today_tasks = [t for t in tasks if t.due_at >= day_start]
    overdue = [t for t in tasks if t.due_at < day_start]
    if today_tasks:
        lines.append("\nЗадачи на сегодня:")
        lines += [f"• {t.title}" + (f" — {_hhmm(t.due_at, tz)}" if t.due_has_time else "") for t in today_tasks[:10]]
    if overdue:
        lines.append(f"\nПросрочено ({len(overdue)}):")
        lines += [f"• {t.title} (с {t.due_at.astimezone(tz):%d.%m})" for t in overdue[:5]]
    if reminders:
        lines.append("\nНапоминания:")
        lines += [f"• {_hhmm(r.next_fire_at, tz)} {r.text}" for r in reminders[:10]]
    if not (today_tasks or overdue or reminders):
        lines.append("\nНа сегодня задач и напоминаний нет.")
    soon = [p for p in sm["upcoming"] if date.fromisoformat(p["next_due"]) <= today + timedelta(days=3)]
    if soon:
        lines.append("\nПлатежи до " + f"{today + timedelta(days=3):%d.%m}:")
        lines += [f"• {p['title']} {p['amount_text']} ({date.fromisoformat(p['next_due']):%d.%m})" for p in soon]
    if sm["budget"]:
        left = f"осталось {fs.rub(sm['remaining'])}" if sm["remaining"] > 0 else f"перерасход {fs.rub(-sm['remaining'])}"
        lines.append(f"\nБюджет месяца: {left}" + (f", {fs.rub(sm['per_day'])} в день" if sm["per_day"] else ""))
    return "\n".join(lines)
