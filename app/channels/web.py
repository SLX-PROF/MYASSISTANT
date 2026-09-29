"""Web channel: pushes notifications to open tabs through the SSE event bus.

Closed tabs catch up on next load: notifications are persisted (unread until
the user opens them), so nothing is lost when no tab is listening.
"""

from __future__ import annotations

from app.channels.base import NotificationPayload, Notifier
from app.events import EventBus


class WebNotifier(Notifier):
    name = "web"

    def __init__(self, bus: EventBus):
        self.bus = bus

    async def send(self, payload: NotificationPayload) -> None:
        self.bus.publish(
            {
                "type": "notification",
                "notification": {
                    "id": payload.notification_id,
                    "kind": payload.kind,
                    "title": payload.title,
                    "body": payload.body,
                    "overdue": payload.overdue,
                    "conversation_id": payload.conversation_id,
                },
                "message": payload.message,
            }
        )
