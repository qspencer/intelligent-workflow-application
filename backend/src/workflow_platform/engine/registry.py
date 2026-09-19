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


class NonRetryableStepFailure(StepFailure):
    """A failure that repeating cannot fix.

    `runtime.retries` fires on ANY `StepFailure`, which is right for a
    timeout or a transient API error and wrong for a configuration fault:
    an unresolvable pinned parameter resolves to the same nothing every
    time. Observed 2026-09-19 — a dry run with an unresolvable pin burned
    three agentic attempts, each a real Bedrock dispatch, to reach the same
    conclusion three times.

    A subclass of `StepFailure`, so every existing handler (the engine's
    failure path, the workflow-level `except StepFailure`) keeps working
    unchanged; only the retry loop looks for the narrower type.

    This is the cheap, static half of EXECUTION_SEMANTICS §4's
    effect-gating item: it does not classify errors at runtime, it lets the
    RAISER say "not worth repeating" where the raiser already knows.
    """


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
