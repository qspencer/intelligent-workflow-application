"""FunctionRegistry — name → async callable lookup for deterministic step functions.

Deterministic step signature:
    async fn(config: dict, context: WorkflowContext, world: World) -> dict[str, Any]

The function returns its output dict; the engine stores it in context under the
step id. On unrecoverable failure, raise `StepFailure` with a message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from workflow_platform.engine.context import WorkflowContext
from workflow_platform.world import World

StepFunction = Callable[[dict[str, Any], WorkflowContext, World], Awaitable[dict[str, Any]]]


class StepFailure(Exception):
    """Raised by a deterministic step function to mark unrecoverable failure."""


class FunctionRegistry:
    def __init__(self, functions: dict[str, StepFunction] | None = None) -> None:
        self._fns: dict[str, StepFunction] = dict(functions or {})

    def register(self, name: str, fn: StepFunction) -> None:
        if name in self._fns:
            raise ValueError(f"Step function {name!r} is already registered")
        self._fns[name] = fn

    def get(self, name: str) -> StepFunction | None:
        return self._fns.get(name)

    def names(self) -> list[str]:
        return sorted(self._fns)

    def __len__(self) -> int:
        return len(self._fns)

    def __contains__(self, name: object) -> bool:
        return name in self._fns
