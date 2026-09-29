from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.channels.base import NotificationPayload, Notifier
from app.db.models import Message, Notification, Reminder
from app.scheduler import ReminderScheduler
from app.services import items

T0 = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)


class RecordingNotifier(Notifier):
    def __init__(self):
        self.sent: list[NotificationPayload] = []

    async def send(self, payload):
        self.sent.append(payload)


async def _add(db, settings, when, kind="none", weekdays=None, text="Выпить воды"):
    async with db.session() as s:
        r = await items.create_reminder(
            s, text=text, when=when, tz=settings.tz, kind=kind, weekdays=weekdays, now=when - timedelta(minutes=5)
        )
        await s.commit()
        return r.id


async def test_fires_when_due_and_not_before(db, settings):
    rid = await _add(db, settings, T0 + timedelta(minutes=2))
    n = RecordingNotifier()
    sched = ReminderScheduler(db, settings, n)

    assert await sched.fire_due(now=T0 + timedelta(minutes=1)) == 0
    assert await sched.fire_due(now=T0 + timedelta(minutes=2, seconds=1)) == 1
    assert len(n.sent) == 1 and n.sent[0].overdue is False
    assert n.sent[0].message["role"] == "notification"

    async with db.session() as s:
        r = await s.get(Reminder, rid)
        assert r.status == "done" and r.next_fire_at is None
        notes = (await s.scalars(select(Notification))).all()
        assert len(notes) == 1 and notes[0].read_at is None
        msgs = (await s.scalars(select(Message).where(Message.role == "notification"))).all()
        assert len(msgs) == 1 and msgs[0].cards[0]["type"] == "reminder_fired"

    # idempotent: nothing fires twice
    assert await sched.fire_due(now=T0 + timedelta(minutes=10)) == 0


async def test_recurring_reschedules(db, settings):
    rid = await _add(db, settings, T0, kind="daily")
    sched = ReminderScheduler(db, settings, RecordingNotifier())
    assert await sched.fire_due(now=T0 + timedelta(seconds=5)) == 1
    async with db.session() as s:
        r = await s.get(Reminder, rid)
        assert r.status == "active"
        assert r.next_fire_at == T0 + timedelta(days=1)
        assert r.fire_count == 1


async def test_missed_while_down_are_delivered_as_overdue(db, settings):
    """Simulates a restart: reminders due during downtime are not lost."""
    one_shot = await _add(db, settings, T0)
    daily = await _add(db, settings, T0, kind="daily", text="Витамины")

    # "Server was down" for 3 days; a fresh scheduler instance starts afterwards.
    after_restart = T0 + timedelta(days=3, hours=1)
    n = RecordingNotifier()
    sched = ReminderScheduler(db, settings, n, clock=lambda: after_restart)
    await sched.start()
    try:
        assert len(n.sent) == 2
        assert all(p.overdue for p in n.sent)
        async with db.session() as s:
            assert (await s.get(Reminder, one_shot)).status == "done"
            d = await s.get(Reminder, daily)
            # jumps to the next future occurrence, no burst of old ones
            assert d.next_fire_at == T0 + timedelta(days=4)
        assert sched._sched.get_job(f"reminder-{daily}") is not None
    finally:
        await sched.shutdown()


async def test_active_reminders_rescheduled_on_start(db, settings):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    rid = await _add(db, settings, future)
    sched = ReminderScheduler(db, settings, RecordingNotifier())
    await sched.start()
    try:
        job = sched._sched.get_job(f"reminder-{rid}")
        assert job is not None
        assert abs((job.next_run_time - future).total_seconds()) < 1
    finally:
        await sched.shutdown()


async def test_cancelled_reminder_does_not_fire(db, settings):
    rid = await _add(db, settings, T0)
    async with db.session() as s:
        await items.cancel_reminder(s, rid)
        await s.commit()
    sched = ReminderScheduler(db, settings, RecordingNotifier())
    assert await sched.fire_due(now=T0 + timedelta(minutes=5)) == 0
