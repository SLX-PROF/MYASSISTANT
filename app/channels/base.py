"""Notification channels.

The core (scheduler) persists every notification and the chat message itself,
then hands a channel-neutral payload to the configured Notifier(s). Adding
Telegram, ntfy or e-mail later means writing one more Notifier subclass and
registering it in `build_notifier()`; the core does not change.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class NotificationPayload:
    notification_id: int
    kind: str  # "reminder"
    title: str
    body: str
    overdue: bool = False
    conversation_id: int | None = None
    message: dict | None = None  # serialised chat message, for the web UI
    extra: dict = field(default_factory=dict)


class Notifier(ABC):
    name: str = "base"

    @abstractmethod
    async def send(self, payload: NotificationPayload) -> None: ...


class CompositeNotifier(Notifier):
    """Fans out to several channels; one failing channel does not block others."""

    name = "composite"

    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    async def send(self, payload: NotificationPayload) -> None:
        results = await asyncio.gather(*(n.send(payload) for n in self.notifiers), return_exceptions=True)
        for n, r in zip(self.notifiers, results):
            if isinstance(r, Exception):
                log.error("notifier %s failed: %s", n.name, r.__class__.__name__)
