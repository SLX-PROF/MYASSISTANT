"""Noise-free alerting (docs/MONITORING_SPEC.md, "Тревоги без шума").

* ok -> silence; a resolved problem sends one "Восстановлено" with its duration;
* the same warning repeats at most every 6 h, an alarm at most every hour;
* escalation (warning -> alarm) is sent immediately;
* /mute silences warnings only; alarms always go through.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.db.models import Alert, utcnow
from app.db.session import Database
from app.monitor.checks import ALARM, LEVEL_ORDER, OK, WARN, CheckResult
from app.monitor.messenger import Messenger
from app.services import kv

log = logging.getLogger(__name__)

REPEAT = {WARN: timedelta(hours=6), ALARM: timedelta(hours=1)}
PREFIX = {WARN: "Предупреждение", ALARM: "Тревога"}
MUTE_KEY = "mute_until"

Explainer = Callable[[CheckResult, list[CheckResult]], Awaitable[str | None]]


def human_duration(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{hours // 24} дн {hours % 24} ч"


class AlertManager:
    def __init__(self, db: Database, messenger: Messenger, explainer: Explainer | None = None, clock=utcnow):
        self.db = db
        self.messenger = messenger
        self.explainer = explainer
        self.clock = clock

    async def mute_until(self) -> datetime | None:
        async with self.db.session() as s:
            v = await kv.get(s, MUTE_KEY)
        return datetime.fromisoformat(v) if v else None

    async def set_mute(self, until: datetime | None) -> None:
        async with self.db.session() as s:
            await kv.put(s, MUTE_KEY, until.isoformat() if until else None)
            await s.commit()

    async def process(self, results: list[CheckResult]) -> list[str]:
        """Apply results; returns the texts that were sent."""
        now = self.clock()
        mute = await self.mute_until()
        muted = mute is not None and mute > now
        to_send: list[tuple[str, CheckResult | None]] = []

        async with self.db.session() as s:
            for r in results:
                alert = await s.get(Alert, r.key)
                if r.level == OK:
                    if alert is not None:
                        if alert.last_sent_at is not None:
                            to_send.append(
                                (f"Восстановлено: {alert.title}. Проблема длилась {human_duration(now - alert.started_at)}.", None)
                            )
                        await s.delete(alert)
                    continue

                new = alert is None
                escalated = not new and LEVEL_ORDER[r.level] > LEVEL_ORDER[alert.level]
                if new:
                    alert = Alert(key=r.key, level=r.level, title=r.title, detail=r.detail, started_at=now)
                    s.add(alert)
                else:
                    alert.level, alert.title, alert.detail = r.level, r.title, r.detail
                if muted and r.level == WARN:
                    continue
                due = alert.last_sent_at is None or now - alert.last_sent_at >= REPEAT[r.level]
                if not (new or escalated or due):
                    continue
                first = alert.last_sent_at is None or escalated
                head = PREFIX[r.level] + ": " + r.title
                if not first:
                    head = f"Всё ещё {PREFIX[r.level].lower()}: {r.title} (с {human_duration(now - alert.started_at)} назад)"
                text = head + (f"\n{r.detail}" if r.detail else "")
                alert.last_sent_at = now
                to_send.append((text, r if first else None))
            await s.commit()

        sent = []
        for text, explain_for in to_send:
            if explain_for is not None and self.explainer is not None:
                try:
                    note = await self.explainer(explain_for, results)
                except Exception:  # noqa: BLE001 - explanation is optional
                    log.exception("explainer failed")
                    note = None
                if note:
                    text += "\n\n" + note
            await self.messenger.send(text, title="Мониторинг")
            sent.append(text)
        return sent

    async def active(self) -> list[Alert]:
        from sqlalchemy import select

        async with self.db.session() as s:
            return list((await s.scalars(select(Alert).order_by(Alert.started_at))).all())
