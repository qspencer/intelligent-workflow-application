"""Trigger plugin interface.

A `Trigger` is an event source. When started, it watches some external surface
(files, HTTP, schedule) and invokes its `on_event` callback for each event,
passing a JSON-serializable payload. The callback is responsible for creating
a workflow instance — typically by calling `WorkflowEngine.run`.

For Phase 0 / Week 3 only `FilesystemTrigger` is implemented. Webhook +
schedule triggers slot in via the same shape in Phase 1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

TriggerCallback = Callable[[dict[str, Any]], Awaitable[None]]


class Trigger(ABC):
    type: ClassVar[str]

    @abstractmethod
    async def start(self, on_event: TriggerCallback) -> None:
        """Begin watching. Calls `on_event` once per event with a payload dict."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop watching. Idempotent."""
