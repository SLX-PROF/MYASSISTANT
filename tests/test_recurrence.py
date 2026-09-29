from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services.recurrence import build_rule, describe, next_occurrence

MSK = ZoneInfo("Europe/Moscow")


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_one_shot_has_no_next():
    assert next_occurrence({"kind": "none"}, utc(2026, 1, 1)) is None


def test_daily_keeps_local_time():
    first = datetime(2026, 9, 30, 10, 0, tzinfo=MSK)
    rule = build_rule("daily", first, MSK)
    assert rule == {"kind": "daily", "time": "10:00", "tz": "Europe/Moscow"}
    nxt = next_occurrence(rule, first.astimezone(timezone.utc))
    assert nxt.astimezone(MSK) == datetime(2026, 10, 1, 10, 0, tzinfo=MSK)


def test_daily_across_dst_keeps_wall_clock():
    ny = ZoneInfo("America/New_York")
    first = datetime(2026, 3, 7, 9, 0, tzinfo=ny)  # DST starts 2026-03-08
    rule = build_rule("daily", first, ny)
    nxt = next_occurrence(rule, first.astimezone(timezone.utc))
    assert nxt.astimezone(ny).hour == 9
    assert nxt.astimezone(ny).day == 8


def test_weekly_picks_next_selected_day():
    first = datetime(2026, 9, 28, 8, 30, tzinfo=MSK)  # Monday
    rule = build_rule("weekly", first, MSK, weekdays=[0, 2])  # Mon, Wed
    nxt = next_occurrence(rule, first.astimezone(timezone.utc))
    assert nxt.astimezone(MSK) == datetime(2026, 9, 30, 8, 30, tzinfo=MSK)
    nxt2 = next_occurrence(rule, nxt)
    assert nxt2.astimezone(MSK) == datetime(2026, 10, 5, 8, 30, tzinfo=MSK)


def test_monthly_clamps_without_drift():
    first = datetime(2026, 1, 31, 12, 0, tzinfo=MSK)
    rule = build_rule("monthly", first, MSK)
    feb = next_occurrence(rule, first.astimezone(timezone.utc))
    assert feb.astimezone(MSK).date().isoformat() == "2026-02-28"
    mar = next_occurrence(rule, feb)
    assert mar.astimezone(MSK).date().isoformat() == "2026-03-31"


def test_describe():
    assert describe({"kind": "none"}) == "однократно"
    assert describe({"kind": "weekly", "time": "09:00", "weekdays": [0, 1, 2, 3, 4]}) == "по будням в 09:00"
