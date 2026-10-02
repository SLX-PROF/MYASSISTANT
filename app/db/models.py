"""ORM models. All timestamps are stored in UTC."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in SQLite, always returns tz-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Новый чат")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True
    )


class Message(Base):
    """A chat item.

    role:
      user          - message typed by the user
      assistant     - model output (text and/or tool calls)
      tool          - tool results (sent to the model as a `user` turn)
      notification  - UI-only item (e.g. a fired reminder); never sent to the model
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="")
    # Exact provider content blocks, replayed verbatim to keep history append-only.
    api_content: Mapped[list[Any] | None] = mapped_column(default=None)
    # UI cards produced by tools (reminder created, task, fact...).
    cards: Mapped[list[Any] | None] = mapped_column(default=None)
    provider: Mapped[str | None] = mapped_column(String(40), default=None)
    model: Mapped[str | None] = mapped_column(String(100), default=None)
    # For model output: id of the first message of the history window that was
    # sent to produce it. Reasoning blocks are replayed only while it matches.
    context_start_id: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(500))
    # Next time the reminder should fire; None once finished.
    next_fire_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    # {"kind": "none|daily|weekly|monthly", "weekdays": [0..6], "anchor_day": 1..31}
    recurrence: Mapped[dict[str, Any]] = mapped_column(default=lambda: {"kind": "none"})
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|done|cancelled
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(default=None)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    due_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    # False when only a date was given ("tomorrow"); due_at is then local midnight.
    due_has_time: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|done
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), default="reminder")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_id: Mapped[int | None] = mapped_column(
        ForeignKey("reminders.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), default=None
    )
    read_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Lead(Base):
    """A site lead as received from the feed. Never contains personal data."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)  # site's id
    created_at: Mapped[datetime] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(30))
    notified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)


class Alert(Base):
    """Currently active monitoring problem (one row per check key)."""

    __tablename__ = "alerts"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    level: Mapped[str] = mapped_column(String(10))  # warn | alarm
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_sent_at: Mapped[datetime | None] = mapped_column(default=None)


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # chat | monitor | telegram
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Estimated cost in micro-dollars (integer, no float drift).
    cost_micro_usd: Mapped[int] = mapped_column(Integer, default=0)


class RegularTask(Base):
    """A recurring chore that must be confirmed with /done."""

    __tablename__ = "regular_tasks"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20), default="reminder")  # reminder | external
    high_stakes: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(default=dict)  # recurrence rule
    runbook: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_due_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    # Original due date while snoozed (limits total snooze to 14 days).
    snoozed_from: Mapped[datetime | None] = mapped_column(default=None)
    last_done_at: Mapped[datetime | None] = mapped_column(default=None)
    # Highest escalation stage already notified for the current due date.
    stage: Mapped[int] = mapped_column(Integer, default=0)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    # "all" = the owner; "content" = content calendar only.
    scope: Mapped[str] = mapped_column(String(20), default="all", server_default="all")


# ------------------------------------------------------------ content plan


class ContentItem(Base):
    """One planned post/video. day=None means it sits in the idea bank."""

    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    rubric: Mapped[str] = mapped_column(String(30), default="")
    icon: Mapped[str] = mapped_column(String(20), default="sparkles")
    stage: Mapped[str] = mapped_column(String(20), default="idea")  # idea|script|filmed|published
    publish_time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM
    platforms: Mapped[str] = mapped_column(String(100), default="", server_default="")  # comma separated
    hook: Mapped[str] = mapped_column(String(300), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ContentRef(Base):
    """A reference attached to a content item: a link or an uploaded photo."""

    __tablename__ = "content_refs"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # link | photo
    url: Mapped[str] = mapped_column(String(1000), default="")
    file: Mapped[str] = mapped_column(String(100), default="")  # stored photo name under data/media/content
    caption: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- finance


class FinCategory(Base):
    __tablename__ = "fin_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="expense")  # expense | income
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)  # rubles, 0 = no limit
    color: Mapped[str] = mapped_column(String(9), default="#22d3ee")
    keywords: Mapped[str] = mapped_column(Text, default="")  # comma separated, for quick input
    position: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class FinTransaction(Base):
    __tablename__ = "fin_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(Integer)  # kopecks, always positive
    kind: Mapped[str] = mapped_column(String(10), default="expense")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("fin_categories.id", ondelete="SET NULL"), default=None, index=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FinRecurring(Base):
    """A regular payment (rent, subscriptions) with a reminder before the due date."""

    __tablename__ = "fin_recurring"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)  # kopecks
    category_id: Mapped[int | None] = mapped_column(ForeignKey("fin_categories.id", ondelete="SET NULL"), default=None)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)
    interval_months: Mapped[int] = mapped_column(Integer, default=1)
    next_due: Mapped[date] = mapped_column(Date, index=True)
    remind_days: Mapped[int] = mapped_column(Integer, default=2)
    reminded_for: Mapped[date | None] = mapped_column(Date, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FinGoal(Base):
    """A savings goal: «отпуск 150 000 ₽ к июню»."""

    __tablename__ = "fin_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    target: Mapped[int] = mapped_column(Integer)  # kopecks
    saved: Mapped[int] = mapped_column(Integer, default=0)  # kopecks
    deadline: Mapped[date | None] = mapped_column(Date, default=None)
    color: Mapped[str] = mapped_column(String(9), default="#5eead4")
    done_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------ notes


class Note(Base):
    """A saved thought or link («второй мозг»)."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(300), default="")  # page title for links
    tags: Mapped[str] = mapped_column(String(300), default="")  # comma separated
    source: Mapped[str] = mapped_column(String(20), default="chat")  # chat | telegram | forward | web | voice
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
