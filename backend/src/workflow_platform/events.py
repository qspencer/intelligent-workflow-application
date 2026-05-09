"""In-process event bus.

Engine audit events are mirrored to the bus; WebSocket subscribers receive a
JSON-serializable copy in real time. For multi-process deployments this needs
a shared backend (Redis pub/sub, NATS, etc.) — Phase 2 work.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            # Slow subscriber: drop the event for them rather than backpressure.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        queue = self.subscribe()
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self.unsubscribe(queue)
