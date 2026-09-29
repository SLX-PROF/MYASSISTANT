"""In-process pub/sub used to push live events (SSE) to open browser tabs.

The app runs as a single process (one uvicorn worker), so an in-memory bus is
enough. Events are small dicts: {"type": "...", ...}.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, queue_size: int = 100):
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("dropping event for slow subscriber")

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)
