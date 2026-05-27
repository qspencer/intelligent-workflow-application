"""Connector plugin interface.

Per `docs/INTEGRATIONS.md`, a connector is bidirectional: it can be a *trigger*
(event source — e.g. file dropped in S3, message in Slack) or a *destination*
(write target — e.g. PutObject to S3, post to Slack channel) or both. The same
plugin handles both directions.

For Phase 2 / Week 7: only `WebhookConnector` and `S3Connector` are implemented.
M365 / Google / Slack stay deferred until a workload pulls them in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Self

ConnectorEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class Connector(ABC):
    type: ClassVar[str]

    @abstractmethod
    async def authenticate(self) -> None:
        """Establish or refresh credentials. Idempotent."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True iff the connector can reach its target system."""

    # --- trigger side (default: not a trigger) ---

    async def trigger_listen(self, on_event: ConnectorEventCallback) -> None:
        """Push-based event source. Default: this connector isn't a push trigger."""
        return None

    async def trigger_poll(self) -> list[dict[str, Any]]:
        """Pull-based event source. Returns new events since the last call.
        Default: this connector isn't a pull trigger."""
        return []

    async def trigger_stop(self) -> None:
        """Stop the trigger half. Default: no-op."""
        return None

    # --- destination side (default: not a destination) ---

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write to the target system. Returns response metadata."""
        raise NotImplementedError(f"{self.type!r} is not a send-capable connector")

    async def query(self, params: dict[str, Any]) -> Any:
        """Read from the target system. Shape of the response is connector-specific."""
        raise NotImplementedError(f"{self.type!r} is not a query-capable connector")

    # --- lifecycle (default: no-op so any connector composes as an async ctx mgr) ---
    #
    # Some connectors need per-run setup/teardown — the browser connector
    # launches Chromium in `__aenter__` and closes it in `__aexit__`. Most
    # connectors don't; they're process-scoped and authenticate once. The
    # defaults below let the engine treat every connector uniformly as
    # `async with` without each subclass having to implement no-ops.

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        # `type[BaseException]` would be the proper annotation but `type` is
        # shadowed by this class's `type: ClassVar[str]` field, so mypy
        # can't resolve it here. `Any` is fine — context-manager dunders
        # have a fixed shape that callers never inspect.
        return None
