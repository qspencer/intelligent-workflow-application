"""WebhookTrigger — event source for HTTP POSTs.

Each `WebhookTrigger` registers itself with a shared `WebhookRegistry` keyed by
`trigger_id`. The FastAPI app exposes a single endpoint
(`POST /api/triggers/webhook/{trigger_id}`) that looks up the trigger and
invokes its callback with the request body. This keeps the trigger plugin
shape generic — webhooks slot in via the same `start`/`stop` interface as the
filesystem trigger, and proves the abstraction is reusable.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.triggers.base import Trigger, TriggerCallback


class WebhookRegistry:
    """In-process registry mapping `trigger_id` → callback."""

    def __init__(self) -> None:
        self._callbacks: dict[str, TriggerCallback] = {}

    def register(self, trigger_id: str, callback: TriggerCallback) -> None:
        if trigger_id in self._callbacks:
            raise ValueError(f"Webhook trigger {trigger_id!r} already registered")
        self._callbacks[trigger_id] = callback

    def unregister(self, trigger_id: str) -> None:
        self._callbacks.pop(trigger_id, None)

    def is_registered(self, trigger_id: str) -> bool:
        return trigger_id in self._callbacks

    async def fire(self, trigger_id: str, payload: dict[str, Any]) -> bool:
        """Return True if a callback was found and invoked, False otherwise."""
        callback = self._callbacks.get(trigger_id)
        if callback is None:
            return False
        await callback(payload)
        return True


class WebhookTrigger(Trigger):
    type: ClassVar[str] = "webhook"

    def __init__(self, registry: WebhookRegistry, trigger_id: str) -> None:
        self.registry = registry
        self.trigger_id = trigger_id

    async def start(self, on_event: TriggerCallback) -> None:
        self.registry.register(self.trigger_id, on_event)

    async def stop(self) -> None:
        self.registry.unregister(self.trigger_id)
