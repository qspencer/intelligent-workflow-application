"""WebhookTrigger — event source for HTTP POSTs.

Each `WebhookTrigger` registers itself with a shared `WebhookRegistry` keyed by
`trigger_id`. The FastAPI app exposes a single endpoint
(`POST /api/triggers/webhook/{trigger_id}`) that looks up the trigger and
invokes its callback with the request body. This keeps the trigger plugin
shape generic — webhooks slot in via the same `start`/`stop` interface as the
filesystem trigger, and proves the abstraction is reusable.

Security (G2): a trigger may carry a `secret_name` — a `SecretStore` key whose
value is the HMAC shared secret. When set, the HTTP endpoint requires a valid
GitHub-style `X-Hub-Signature-256: sha256=<hex>` over the raw request body and
rejects everything else. When unset, the trigger accepts unsigned posts (the
local-dev path) — production workflow YAMLs should always set `secret_name`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.triggers.base import Trigger, TriggerCallback


class WebhookRegistry:
    """In-process registry mapping `trigger_id` → (callback, secret_name)."""

    def __init__(self) -> None:
        self._callbacks: dict[str, TriggerCallback] = {}
        self._secret_names: dict[str, str] = {}

    def register(
        self, trigger_id: str, callback: TriggerCallback, *, secret_name: str | None = None
    ) -> None:
        if trigger_id in self._callbacks:
            raise ValueError(f"Webhook trigger {trigger_id!r} already registered")
        self._callbacks[trigger_id] = callback
        if secret_name:
            self._secret_names[trigger_id] = secret_name

    def unregister(self, trigger_id: str) -> None:
        self._callbacks.pop(trigger_id, None)
        self._secret_names.pop(trigger_id, None)

    def is_registered(self, trigger_id: str) -> bool:
        return trigger_id in self._callbacks

    def secret_name(self, trigger_id: str) -> str | None:
        """The SecretStore key holding this trigger's HMAC secret, if secured."""
        return self._secret_names.get(trigger_id)

    async def fire(self, trigger_id: str, payload: dict[str, Any]) -> bool:
        """Return True if a callback was found and invoked, False otherwise."""
        callback = self._callbacks.get(trigger_id)
        if callback is None:
            return False
        await callback(payload)
        return True


class WebhookTrigger(Trigger):
    type: ClassVar[str] = "webhook"

    def __init__(
        self, registry: WebhookRegistry, trigger_id: str, *, secret_name: str | None = None
    ) -> None:
        self.registry = registry
        self.trigger_id = trigger_id
        self.secret_name = secret_name

    async def start(self, on_event: TriggerCallback) -> None:
        self.registry.register(self.trigger_id, on_event, secret_name=self.secret_name)

    async def stop(self) -> None:
        self.registry.unregister(self.trigger_id)
