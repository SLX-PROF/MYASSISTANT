"""Reminder scheduler.

Source of truth is the `reminders` table (`next_fire_at`), so reminders survive
restarts by construction. APScheduler drives delivery:

* one exact-time job per active reminder (rebuilt from the DB on startup);
* a periodic sweep as a safety net (and for anything scheduled while down).

Every trigger calls the same idempotent `fire_due()`, guarded by a lock.
Reminders that were due while the app was down are delivered on startup,
marked "overdue", instead of being lost. Recurring reminders jump to their
next future occurrence after firing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.channels.base import NotificationPayload, Notifier
from app.config import Settings
from app.db.models import Message, Notification, Reminder, utcnow
from app.db.session import Database
from app.services import recurrence as rec
from app.services.chat import message_out, notification_target, touch
from app.services.timeparse import to_utc_iso

log = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.db = db
        self.settings = settings
        self.notifier = notifier
        self.clock = clock
        self.grace = timedelta(seconds=settings.reminder_grace_seconds)
        self._lock = asyncio.Lock()
        self._sched: AsyncIOScheduler | None = None

    # ----------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._sched = AsyncIOScheduler(timezone=timezone.utc)
        self._sched.start()
        self._sched.add_job(
            self.fire_due,
            IntervalTrigger(seconds=self.settings.scheduler_sweep_seconds),
            id="sweep",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        delivered = await self.fire_due()
        if delivered:
            log.info("delivered %d reminder(s) missed while offline", delivered)
        await self._schedule_all()

    async def shutdown(self) -> None:
        if self._sched and self._sched.running:
            self._sched.shutdown(wait=False)
        self._sched = None

    async def _schedule_all(self) -> None:
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    select(Reminder.id, Reminder.next_fire_at).where(
                        Reminder.status == "active", Reminder.next_fire_at.is_not(None)
                    )
                )
            ).all()
        for rid, at in rows:
            self.schedule(rid, at)

    # -------------------------------------------------------------- jobs

    def schedule(self, reminder_id: int, at: datetime | None) -> None:
        if self._sched is None:
            return
        if at is None:
            self.unschedule(reminder_id)
            return
        self._sched.add_job(
            self.fire_due,
            DateTrigger(run_date=at),
            id=f"reminder-{reminder_id}",
            replace_existing=True,
            misfire_grace_time=None,
        )

    def add_job(self, func, trigger, job_id: str) -> None:
        """Register another periodic job on the shared scheduler."""
        if self._sched is None:
            raise RuntimeError("scheduler not started")
        self._sched.add_job(func, trigger, id=job_id, replace_existing=True, coalesce=True, max_instances=1)

    def unschedule(self, reminder_id: int) -> None:
        if self._sched is None:
            return
        try:
            self._sched.remove_job(f"reminder-{reminder_id}")
        except JobLookupError:
            pass

    # ----------------------------------------------------------- delivery

    async def fire_due(self, now: datetime | None = None) -> int:
        """Deliver every active reminder whose time has come. Returns count."""
        async with self._lock:
            now = now or self.clock()
            payloads: list[NotificationPayload] = []
            reschedule: list[tuple[int, datetime | None]] = []
            async with self.db.session() as s:
                due = (
                    await s.scalars(
                        select(Reminder)
                        .where(Reminder.status == "active", Reminder.next_fire_at <= now)
                        .order_by(Reminder.next_fire_at)
                    )
                ).all()
                for r in due:
                    payloads.append(await self._deliver(s, r, now))
                    reschedule.append((r.id, r.next_fire_at))
                await s.commit()

        for rid, at in reschedule:
            self.schedule(rid, at)
        for p in payloads:
            try:
                await self.notifier.send(p)
            except Exception:  # noqa: BLE001 - persisted already; live push is best-effort
                log.exception("live notification failed")
        return len(payloads)

    async def _deliver(self, s, r: Reminder, now: datetime) -> NotificationPayload:
        scheduled_for = r.next_fire_at
        overdue = scheduled_for is not None and now - scheduled_for > self.grace
        nxt = rec.next_occurrence(r.recurrence, now)
        r.last_fired_at = now
        r.fire_count += 1
        if nxt is None:
            r.status = "done"
        r.next_fire_at = nxt

        conv = await notification_target(s, r.conversation_id)
        touch(conv)
        title = "Просроченное напоминание" if overdue else "Напоминание"
        card = {
            "type": "reminder_fired",
            "reminder_id": r.id,
            "text": r.text,
            "overdue": overdue,
            "scheduled_for": to_utc_iso(scheduled_for),
            "fired_at": to_utc_iso(now),
            "recurrence_label": rec.describe(r.recurrence),
            "next_fire_at": to_utc_iso(nxt),
        }
        msg = Message(conversation_id=conv.id, role="notification", text=f"{title}: {r.text}", cards=[card])
        s.add(msg)
        await s.flush()
        n = Notification(
            kind="reminder",
            title=title,
            body=r.text,
            overdue=overdue,
            reminder_id=r.id,
            conversation_id=conv.id,
            message_id=msg.id,
        )
        s.add(n)
        await s.flush()
        log.info("reminder %s fired (overdue=%s)", r.id, overdue)
        return NotificationPayload(
            notification_id=n.id,
            kind="reminder",
            title=title,
            body=r.text,
            overdue=overdue,
            conversation_id=conv.id,
            message=message_out(msg),
        )
