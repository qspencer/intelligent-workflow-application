"""In-process event bus.

Engine audit events are mirrored to the bus; WebSocket subscribers receive a
JSON-serializable copy in real time. For multi-process deployments this needs
a shared backend (Redis pub/sub, NATS, etc.) — Phase 2 work.

Org enrichment (ROLES_PLAN §4b): when an `org_resolver` is configured,
`publish` stamps `org_id` on any event that references a workflow instance —
resolved once per instance (cached), at emit time, so the WS filter never
does per-subscriber lookups. Events with no instance carry no org and are
platform-operator data (Administrators only, enforced by the WS router).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

OrgResolver = Callable[[str], Awaitable[str | None]]


class EventBus:
    def __init__(self, queue_size: int = 256, org_resolver: OrgResolver | None = None) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._org_resolver = org_resolver
        self._org_cache: dict[str, str | None] = {}

    def set_org_resolver(self, resolver: OrgResolver) -> None:
        self._org_resolver = resolver

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        instance_id = event.get("workflow_instance_id")
        if self._org_resolver is not None and instance_id and "org_id" not in event:
            if instance_id not in self._org_cache:
                with contextlib.suppress(Exception):
                    self._org_cache[instance_id] = await self._org_resolver(str(instance_id))
            org = self._org_cache.get(instance_id)
            if org is not None:
                event = {**event, "org_id": org}
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
