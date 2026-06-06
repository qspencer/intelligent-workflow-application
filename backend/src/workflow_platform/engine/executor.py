"""WorkflowEngine — parallel DAG executor with conditional edges, retries,
timeouts, pause/resume, and per-step memory injection.

Independent steps run concurrently (asyncio.wait FIRST_COMPLETED). After a
source step completes, each outgoing edge is evaluated; inactive edges
contribute "resolved but not active" to the target. A target that has all
incoming edges resolved with zero active is marked SKIPPED, and the skip
propagates downstream.

Per-step retries and per-step / per-workflow timeouts are wrapped around
`_run_step_once`. Pause is detected by re-reading the instance state from the
repo between iterations of the dispatch loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import simpleeval

from workflow_platform.agent import Agent, AgentPolicy
from workflow_platform.agent.registry import ToolRegistry as AgentToolRegistry
from workflow_platform.bedrock import BedrockClient
from workflow_platform.connectors.browser import BrowserConnector, PlaywrightConnector
from workflow_platform.cost import cost_for_usage
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.registry import FunctionRegistry, StepFailure
from workflow_platform.events import EventBus
from workflow_platform.memory import MemoryManager
from workflow_platform.observability import Metrics, NoopMetrics
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.persistence.models import _new_id, _utcnow
from workflow_platform.security import CapabilityPolicy, resolve_capabilities
from workflow_platform.security.capabilities import ResolvedCapabilities
from workflow_platform.tools import Tool, ToolContext
from workflow_platform.workflow import (
    AgenticStep,
    DeterministicStep,
    Edge,
    WorkflowDefinition,
    validate_and_order,
)
from workflow_platform.world import World

logger = logging.getLogger(__name__)


class _PauseRequested(Exception):
    """Internal signal: the instance was paused externally; bail out cleanly."""


class _KillRequested(Exception):
    """Internal signal: the instance was killed externally; bail out cleanly.

    Distinct from pause: KILLED is terminal — the instance cannot be resumed.
    """


class ToolCatalog:
    """Name-keyed catalog of all tools available to agentic steps."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} already in catalog")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        """All registered tool names, sorted. Used by the capabilities view."""
        return sorted(self._tools)


@dataclass
class _DagState:
    """Bookkeeping for parallel DAG execution.

    `incoming_total[id]` — number of incoming edges (fixed).
    `incoming_resolved[id]` — number of incoming edges whose source has resolved
        (completed-active, completed-inactive, or skipped).
    `incoming_active[id]` — number of resolved incoming edges that were active.
        A step runs when resolved == total AND active > 0; it's skipped when
        resolved == total AND active == 0.
    """

    incoming_total: dict[str, int] = field(default_factory=dict)
    incoming_resolved: dict[str, int] = field(default_factory=dict)
    incoming_active: dict[str, int] = field(default_factory=dict)
    edges_by_source: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))


_BROWSER_TOOL_PREFIX = "browser_"


def _workflow_uses_browser(definition: WorkflowDefinition) -> bool:
    """True iff any agentic step's `tools` list references a `browser_*` tool.

    Determines whether `_drive` should lazy-build a `BrowserConnector` for
    this run. Workflows that don't touch the browser pay zero cost.
    """
    for step in definition.steps:
        if isinstance(step, AgenticStep):
            for tool_name in step.tools:
                if tool_name.startswith(_BROWSER_TOOL_PREFIX):
                    return True
    return False


@dataclass
class WorkflowEngine:
    repositories: Repositories
    functions: FunctionRegistry
    tools: ToolCatalog
    bedrock: BedrockClient
    world: World
    system_capabilities: CapabilityPolicy | None = None
    memory: MemoryManager | None = None
    events: EventBus | None = None
    metrics: Metrics = field(default_factory=NoopMetrics)
    pause_check_interval: float = 0.0  # seconds; 0 disables polling-based pause checks
    # Browser-connector run-scope config. `browser_downloads_dir` is where
    # `download_via_click` and `screenshot` files land; defaults to a
    # process-local `./downloads/` folder. `browser_connector_factory` is
    # the injection seam for tests: when None, `_build_run_connectors`
    # constructs a default `PlaywrightConnector`; tests pass a factory
    # that returns a fake instead so no real Chromium is launched.
    browser_downloads_dir: Path = field(default_factory=lambda: Path("./downloads"))
    browser_connector_factory: Callable[[], BrowserConnector] | None = None

    # --- public API ---

    async def run(
        self,
        definition: WorkflowDefinition,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        validate_and_order(definition)

        started = _utcnow()
        instance = WorkflowInstance(
            workflow_id=definition.id,
            state=WorkflowInstanceState.RUNNING,
            trigger_payload=dict(trigger_payload or {}),
            started_at=started,
        )
        instance = await self.repositories.instances.create(instance)
        self.metrics.workflow_started(definition.id)
        await self._audit(
            "workflow_started",
            actor_type="engine",
            actor_id="workflow_engine",
            instance_id=instance.id,
            detail={"workflow_id": definition.id, "trigger": instance.trigger_payload},
        )

        context = WorkflowContext(
            instance_id=instance.id,
            workflow_id=definition.id,
            trigger=dict(instance.trigger_payload),
        )
        result = await self._drive(definition, instance, context, already_done=set())
        self._record_workflow_finished(definition.id, result, started)
        return result

    async def resume(self, definition: WorkflowDefinition, instance_id: str) -> WorkflowInstance:
        """Resume a paused instance. Replays no completed work; picks up where
        the previous run left off."""
        instance = await self.repositories.instances.get(instance_id)
        if instance is None:
            raise ValueError(f"Instance {instance_id} not found")
        if instance.state != WorkflowInstanceState.PAUSED:
            raise ValueError(f"Instance {instance_id} is {instance.state.value}, cannot resume")

        resumed_at = _utcnow()
        instance.state = WorkflowInstanceState.RUNNING
        instance = await self.repositories.instances.update(instance)
        await self._audit(
            "workflow_resumed",
            actor_type="engine",
            actor_id="workflow_engine",
            instance_id=instance.id,
            detail={},
        )

        context = WorkflowContext(
            instance_id=instance.id,
            workflow_id=definition.id,
            trigger=dict(instance.trigger_payload),
            steps=dict(instance.context.get("steps", {}) or {}),
        )

        prior = await self.repositories.steps.list_by_instance(instance.id)
        already_done = {
            s.step_id
            for s in prior
            if s.state in (StepExecutionState.COMPLETED, StepExecutionState.SKIPPED)
        }
        result = await self._drive(definition, instance, context, already_done=already_done)
        self._record_workflow_finished(definition.id, result, resumed_at)
        return result

    async def fork(
        self,
        definition: WorkflowDefinition,
        source_instance_id: str,
        from_step_id: str,
    ) -> WorkflowInstance:
        """Fork a prior instance: re-run from `from_step_id` onward in a new
        instance, with the topological ancestors of that step preserved as
        completed (their outputs carried over from the source). Useful for
        rubric / prompt iteration without re-running the upstream work.

        The source instance is unchanged. The new instance gets the same
        trigger payload, copied step executions for the preserved ancestors,
        and runs everything else fresh — including picking up any
        `agent_memory.md` edits that have happened since the source run.
        """
        source = await self.repositories.instances.get(source_instance_id)
        if source is None:
            raise ValueError(f"Instance {source_instance_id} not found")
        step_ids = {s.id for s in definition.steps}
        if from_step_id not in step_ids:
            raise ValueError(f"Step {from_step_id!r} not in workflow {definition.id!r}")

        preserved = _ancestors(definition, from_step_id)
        source_steps = await self.repositories.steps.list_by_instance(source_instance_id)
        usable_outputs: dict[str, dict[str, Any]] = {}
        for s in source_steps:
            if s.step_id in preserved and s.state == StepExecutionState.COMPLETED and s.output:
                usable_outputs[s.step_id] = dict(s.output)

        missing = preserved - usable_outputs.keys()
        if missing:
            raise ValueError(
                f"Cannot fork at {from_step_id!r}: source instance missing completed outputs for "
                f"required ancestor(s) {sorted(missing)}"
            )

        started = _utcnow()
        new_instance = WorkflowInstance(
            workflow_id=definition.id,
            state=WorkflowInstanceState.RUNNING,
            trigger_payload=dict(source.trigger_payload),
            started_at=started,
        )
        new_instance = await self.repositories.instances.create(new_instance)
        self.metrics.workflow_started(definition.id)
        await self._audit(
            "workflow_forked",
            actor_type="engine",
            actor_id="workflow_engine",
            instance_id=new_instance.id,
            detail={
                "source_instance_id": source_instance_id,
                "from_step_id": from_step_id,
                "preserved_step_ids": sorted(preserved),
            },
        )

        # Materialize the preserved steps in the new instance as COMPLETED,
        # carrying over their outputs verbatim.
        for step_id, output in usable_outputs.items():
            await self.repositories.steps.create(
                StepExecution(
                    instance_id=new_instance.id,
                    step_id=step_id,
                    state=StepExecutionState.COMPLETED,
                    started_at=started,
                    completed_at=started,
                    output=output,
                )
            )

        context = WorkflowContext(
            instance_id=new_instance.id,
            workflow_id=definition.id,
            trigger=dict(new_instance.trigger_payload),
            steps={sid: dict(out) for sid, out in usable_outputs.items()},
        )

        result = await self._drive(definition, new_instance, context, already_done=preserved)
        self._record_workflow_finished(definition.id, result, started)
        return result

    def _record_workflow_finished(
        self,
        workflow_id: str,
        instance: WorkflowInstance,
        started: Any,
    ) -> None:
        """Record terminal-state metrics. PAUSED is not terminal, so skip it."""
        if instance.state == WorkflowInstanceState.PAUSED:
            return
        elapsed = (_utcnow() - started).total_seconds()
        self.metrics.workflow_finished(workflow_id, instance.state.value, elapsed)

    # --- core dispatch loop ---

    async def _drive(
        self,
        definition: WorkflowDefinition,
        instance: WorkflowInstance,
        context: WorkflowContext,
        *,
        already_done: set[str],
    ) -> WorkflowInstance:
        """Run the dispatch loop within a connector lifecycle scope.

        Tears down any per-run connectors in `finally` — regardless of
        success / pause / kill / failure path inside `_drive_inner`.
        Connector build itself happens inside `_drive_inner` so a build
        failure routes through the existing `except StepFailure` branch.
        """
        try:
            return await self._drive_inner(definition, instance, context, already_done)
        finally:
            await self._teardown_run_connectors(context, instance.id)

    async def _drive_inner(
        self,
        definition: WorkflowDefinition,
        instance: WorkflowInstance,
        context: WorkflowContext,
        already_done: set[str],
    ) -> WorkflowInstance:
        steps_by_id = {s.id: s for s in definition.steps}
        state = self._build_dag_state(definition, already_done)

        try:
            await self._build_run_connectors(definition, context, instance.id)
            timeout = definition.policies.timeout_seconds
            if timeout:
                await asyncio.wait_for(
                    self._dispatch_loop(
                        definition, steps_by_id, state, context, instance.id, already_done
                    ),
                    timeout=timeout,
                )
            else:
                await self._dispatch_loop(
                    definition, steps_by_id, state, context, instance.id, already_done
                )
        except _PauseRequested:
            instance = await self._mark_instance(
                instance, WorkflowInstanceState.PAUSED, context, error=None
            )
            await self._audit(
                "workflow_paused",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance.id,
            )
            return instance
        except _KillRequested:
            instance = await self._mark_instance(
                instance, WorkflowInstanceState.KILLED, context, error="killed by operator"
            )
            await self._audit(
                "workflow_killed",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance.id,
            )
            return instance
        except TimeoutError as exc:
            instance = await self._mark_instance(
                instance, WorkflowInstanceState.FAILED, context, error="workflow timeout"
            )
            await self._audit(
                "workflow_failed",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance.id,
                detail={"error": "workflow timeout", "exception": str(exc)},
            )
            return instance
        except StepFailure as exc:
            instance = await self._mark_instance(
                instance, WorkflowInstanceState.FAILED, context, error=str(exc)
            )
            await self._audit(
                "workflow_failed",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance.id,
                detail={"error": str(exc)},
            )
            return instance

        instance = await self._mark_instance(
            instance, WorkflowInstanceState.COMPLETED, context, error=None
        )
        await self._audit(
            "workflow_completed",
            actor_type="engine",
            actor_id="workflow_engine",
            instance_id=instance.id,
            detail={"steps": list(context.steps)},
        )
        return instance

    async def _build_run_connectors(
        self,
        definition: WorkflowDefinition,
        context: WorkflowContext,
        instance_id: str,
    ) -> None:
        """Lazy-build per-run connectors required by this workflow.

        Mutates `context.connectors` in place. Audit-logs build failures
        and re-raises as `StepFailure` so the workflow fails cleanly
        instead of crashing inside `_drive`.
        """
        if not _workflow_uses_browser(definition):
            return
        try:
            factory = self.browser_connector_factory or (
                lambda: PlaywrightConnector(downloads_dir=self.browser_downloads_dir)
            )
            connector = factory()
            await connector.__aenter__()
        except Exception as exc:
            await self._audit(
                "connector_build_failed",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance_id,
                detail={"connector": "browser", "error": str(exc)},
            )
            raise StepFailure(f"browser connector build failed: {exc}") from exc
        context.connectors["browser"] = connector
        await self._audit(
            "connector_opened",
            actor_type="engine",
            actor_id="workflow_engine",
            instance_id=instance_id,
            detail={"connector": "browser"},
        )

    async def _teardown_run_connectors(
        self,
        context: WorkflowContext,
        instance_id: str,
    ) -> None:
        """Best-effort teardown for every per-run connector.

        Exceptions during teardown are swallowed + audited; the engine
        must not raise during cleanup or it'll mask the original error.
        """
        for name, connector in list(context.connectors.items()):
            try:
                await connector.__aexit__(None, None, None)
            except Exception as exc:
                logger.exception("Connector %r teardown failed", name)
                await self._audit(
                    "connector_teardown_failed",
                    actor_type="engine",
                    actor_id="workflow_engine",
                    instance_id=instance_id,
                    detail={"connector": name, "error": str(exc)},
                )
            else:
                await self._audit(
                    "connector_closed",
                    actor_type="engine",
                    actor_id="workflow_engine",
                    instance_id=instance_id,
                    detail={"connector": name},
                )
        context.connectors.clear()

    def _build_dag_state(self, definition: WorkflowDefinition, already_done: set[str]) -> _DagState:
        state = _DagState()
        for step in definition.steps:
            state.incoming_total[step.id] = 0
            state.incoming_resolved[step.id] = 0
            state.incoming_active[step.id] = 0
        for edge in definition.edges:
            state.incoming_total[edge.target] += 1
            state.edges_by_source[edge.source].append(edge)
        # Treat already-done steps as if their outgoing edges resolved-active
        # (so resume's downstream knows their parents finished).
        for sid in already_done:
            for edge in state.edges_by_source.get(sid, []):
                state.incoming_resolved[edge.target] += 1
                state.incoming_active[edge.target] += 1
        return state

    async def _dispatch_loop(
        self,
        definition: WorkflowDefinition,
        steps_by_id: dict[str, DeterministicStep | AgenticStep],
        state: _DagState,
        context: WorkflowContext,
        instance_id: str,
        already_done: set[str],
    ) -> None:
        in_progress: dict[str, asyncio.Task[dict[str, Any] | None]] = {}
        # Seed `scheduled` with already-done steps so resume + fork don't
        # re-run them. Their outgoing edges have already been advanced in
        # `_build_dag_state`; downstream scheduling proceeds normally.
        scheduled: set[str] = set(already_done)
        skipped: set[str] = set()

        # Schedule initially-ready steps.
        for sid in steps_by_id:
            if state.incoming_resolved[sid] == state.incoming_total[sid] and sid not in scheduled:
                await self._schedule_or_skip(
                    sid,
                    steps_by_id,
                    state,
                    context,
                    instance_id,
                    in_progress,
                    scheduled,
                    skipped,
                    definition,
                )

        while in_progress:
            await self._maybe_pause(instance_id, in_progress)

            done, _ = await asyncio.wait(in_progress.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                sid = next(s for s, t in in_progress.items() if t is task)
                del in_progress[sid]
                try:
                    task.result()  # raises if step failed
                except StepFailure:
                    await self._cancel_pending(in_progress)
                    raise

                # Step succeeded; check the workflow budget before resolving
                # downstream — pause/escalate must take effect immediately.
                await self._check_budget(definition, context, instance_id, in_progress)

                for edge in state.edges_by_source.get(sid, []):
                    active = self._is_edge_active(edge, context)
                    state.incoming_resolved[edge.target] += 1
                    if active:
                        state.incoming_active[edge.target] += 1
                    if state.incoming_resolved[edge.target] == state.incoming_total[edge.target]:
                        await self._schedule_or_skip(
                            edge.target,
                            steps_by_id,
                            state,
                            context,
                            instance_id,
                            in_progress,
                            scheduled,
                            skipped,
                            definition,
                        )

    async def _schedule_or_skip(
        self,
        sid: str,
        steps_by_id: dict[str, DeterministicStep | AgenticStep],
        state: _DagState,
        context: WorkflowContext,
        instance_id: str,
        in_progress: dict[str, asyncio.Task[Any]],
        scheduled: set[str],
        skipped: set[str],
        definition: WorkflowDefinition,
    ) -> None:
        if sid in scheduled or sid in skipped:
            return
        scheduled.add(sid)

        if state.incoming_total[sid] > 0 and state.incoming_active[sid] == 0:
            # All incoming edges inactive — skip this step and propagate.
            skipped.add(sid)
            await self._record_skip(steps_by_id[sid], instance_id)
            for edge in state.edges_by_source.get(sid, []):
                state.incoming_resolved[edge.target] += 1
                if state.incoming_resolved[edge.target] == state.incoming_total[edge.target]:
                    await self._schedule_or_skip(
                        edge.target,
                        steps_by_id,
                        state,
                        context,
                        instance_id,
                        in_progress,
                        scheduled,
                        skipped,
                        definition,
                    )
            return

        step = steps_by_id[sid]
        capabilities = resolve_capabilities(
            self.system_capabilities, definition.capabilities, step.capabilities
        )
        in_progress[sid] = asyncio.create_task(
            self._run_step_with_retry(step, context, instance_id, capabilities)
        )

    async def _check_budget(
        self,
        definition: WorkflowDefinition,
        context: WorkflowContext,
        instance_id: str,
        in_progress: dict[str, asyncio.Task[Any]],
    ) -> None:
        """Enforce workflow.policies.max_total_tokens with the configured action.

        notify  — audit the breach and continue.
        pause   — audit + raise _PauseRequested to exit the dispatch loop.
        escalate — audit a special escalation entry + pause.
        """
        cap = definition.policies.max_total_tokens
        if cap is None or context.total_tokens <= cap:
            return
        action = definition.policies.budget_action
        detail: dict[str, Any] = {
            "tokens_used": context.total_tokens,
            "tokens_limit": cap,
            "cost_usd": round(context.total_cost_usd, 6),
            "action": action,
        }
        if action == "notify":
            await self._audit(
                "budget_exceeded",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance_id,
                detail=detail,
            )
            return
        if action == "escalate":
            await self._audit(
                "budget_escalated",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance_id,
                detail=detail,
            )
        else:
            await self._audit(
                "budget_exceeded",
                actor_type="engine",
                actor_id="workflow_engine",
                instance_id=instance_id,
                detail=detail,
            )
        await self._cancel_pending(in_progress)
        raise _PauseRequested()

    async def _maybe_pause(
        self, instance_id: str, in_progress: dict[str, asyncio.Task[Any]]
    ) -> None:
        fresh = await self.repositories.instances.get(instance_id)
        if fresh is None:
            return
        if fresh.state == WorkflowInstanceState.PAUSED:
            await self._cancel_pending(in_progress)
            raise _PauseRequested()
        if fresh.state == WorkflowInstanceState.KILLED:
            await self._cancel_pending(in_progress)
            raise _KillRequested()

    @staticmethod
    async def _cancel_pending(in_progress: dict[str, asyncio.Task[Any]]) -> None:
        for task in in_progress.values():
            if not task.done():
                task.cancel()
        if in_progress:
            await asyncio.gather(*in_progress.values(), return_exceptions=True)
        in_progress.clear()

    @staticmethod
    def _is_edge_active(edge: Edge, context: WorkflowContext) -> bool:
        if edge.condition is None:
            return True
        evaluator = simpleeval.SimpleEval(
            names={
                "trigger": context.trigger,
                "steps": context.steps,
                "context": context.model_dump(),
            }
        )
        try:
            return bool(evaluator.eval(edge.condition))
        except Exception:
            logger.exception(
                "Failed to evaluate condition %r — treating as inactive", edge.condition
            )
            return False

    # --- step execution: retry, timeout, lifecycle, audit ---

    async def _run_step_with_retry(
        self,
        step: DeterministicStep | AgenticStep,
        context: WorkflowContext,
        instance_id: str,
        capabilities: ResolvedCapabilities,
    ) -> None:
        runtime = step.runtime
        attempts = runtime.retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                await self._run_step_once(step, context, instance_id, capabilities, attempt)
                return
            except StepFailure as exc:
                last_error = exc
                if attempt < attempts:
                    await self._audit(
                        "step_retry",
                        actor_type="engine",
                        actor_id=f"step:{step.id}",
                        instance_id=instance_id,
                        step_id=step.id,
                        detail={"attempt": attempt, "error": str(exc)},
                    )
                    continue
                raise

        if last_error is not None:
            raise last_error

    async def _run_step_once(
        self,
        step: DeterministicStep | AgenticStep,
        context: WorkflowContext,
        instance_id: str,
        capabilities: ResolvedCapabilities,
        attempt: int,
    ) -> None:
        started_at = _utcnow()
        execution = StepExecution(
            instance_id=instance_id,
            step_id=step.id,
            state=StepExecutionState.RUNNING,
            started_at=started_at,
        )
        execution = await self.repositories.steps.create(execution)
        await self._audit(
            "step_started",
            actor_type="engine",
            actor_id=f"step:{step.id}",
            instance_id=instance_id,
            step_id=step.id,
            detail={"type": step.type, "attempt": attempt},
        )

        try:
            output = await self._dispatch_step(step, context, instance_id, capabilities)
        except (StepFailure, TimeoutError) as exc:
            error_msg = "step timeout" if isinstance(exc, TimeoutError) else str(exc)
            execution.state = StepExecutionState.FAILED
            execution.error = error_msg
            completed_at = _utcnow()
            execution.completed_at = completed_at
            await self.repositories.steps.update(execution)
            self.metrics.step_finished(
                step.type,
                StepExecutionState.FAILED.value,
                (completed_at - started_at).total_seconds(),
            )
            await self._audit(
                "step_failed",
                actor_type="engine",
                actor_id=f"step:{step.id}",
                instance_id=instance_id,
                step_id=step.id,
                detail={"error": error_msg, "attempt": attempt},
            )
            raise StepFailure(error_msg) from exc

        context.record_step_output(step.id, output)
        execution.state = StepExecutionState.COMPLETED
        execution.output = output
        completed_at = _utcnow()
        execution.completed_at = completed_at
        await self.repositories.steps.update(execution)
        self.metrics.step_finished(
            step.type,
            StepExecutionState.COMPLETED.value,
            (completed_at - started_at).total_seconds(),
        )
        await self._audit(
            "step_completed",
            actor_type="engine",
            actor_id=f"step:{step.id}",
            instance_id=instance_id,
            step_id=step.id,
            detail={"output": output, "attempt": attempt},
        )

    async def _dispatch_step(
        self,
        step: DeterministicStep | AgenticStep,
        context: WorkflowContext,
        instance_id: str,
        capabilities: ResolvedCapabilities,
    ) -> dict[str, Any]:
        timeout = step.runtime.timeout_seconds
        coro: Coroutine[Any, Any, dict[str, Any]]
        if isinstance(step, DeterministicStep):
            coro = self._run_deterministic(step, context, capabilities)
        else:
            coro = self._run_agentic(step, context, instance_id, capabilities)
        if timeout is None:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout)

    async def _record_skip(
        self,
        step: DeterministicStep | AgenticStep,
        instance_id: str,
    ) -> None:
        execution = StepExecution(
            instance_id=instance_id,
            step_id=step.id,
            state=StepExecutionState.SKIPPED,
            started_at=_utcnow(),
            completed_at=_utcnow(),
        )
        await self.repositories.steps.create(execution)
        await self._audit(
            "step_skipped",
            actor_type="engine",
            actor_id=f"step:{step.id}",
            instance_id=instance_id,
            step_id=step.id,
        )

    async def _run_deterministic(
        self,
        step: DeterministicStep,
        context: WorkflowContext,
        capabilities: ResolvedCapabilities,
    ) -> dict[str, Any]:
        fn = self.functions.get(step.function)
        if fn is None:
            raise StepFailure(f"Unknown step function: {step.function!r}")
        context.capabilities = capabilities
        return await fn(step.config, context, self.world)

    async def _run_agentic(
        self,
        step: AgenticStep,
        context: WorkflowContext,
        instance_id: str,
        capabilities: ResolvedCapabilities,
    ) -> dict[str, Any]:
        registry = AgentToolRegistry()
        for tool_name in step.tools:
            tool = self.tools.get(tool_name)
            if tool is None:
                raise StepFailure(f"Step {step.id!r} requires unknown tool {tool_name!r}")
            registry.register(tool)

        agent_id = f"steps/{context.workflow_id}/{step.id}"
        memory_text = ""
        if self.memory is not None:
            memory_text = await self.memory.load(agent_id)

        memory_hash: str | None = None
        if memory_text:
            memory_hash = "sha256:" + hashlib.sha256(memory_text.encode()).hexdigest()[:16]

        system_prompt = step.system_prompt or step.goal
        if memory_text:
            system_prompt = f"{system_prompt}\n\n--- Prior agent memory ---\n{memory_text}"

        agent = Agent(
            system_prompt=system_prompt,
            tools=registry,
            model_id=step.model,
            bedrock=self.bedrock,
            policy=AgentPolicy(
                max_iterations=step.policy.max_iterations,
                max_total_tokens=step.policy.max_total_tokens,
                inference_config=step.policy.inference_config,
            ),
        )

        user_message = _build_user_message(step, context)
        tool_ctx = ToolContext(
            world=self.world,
            agent_id=agent_id,
            workflow_instance_id=instance_id,
            capabilities=capabilities,
            connectors=dict(context.connectors),
        )
        result = await agent.run(user_message, context=tool_ctx)

        for call in result.tool_calls:
            await self._audit(
                "tool_call",
                actor_type="agent",
                actor_id=agent_id,
                instance_id=instance_id,
                step_id=step.id,
                detail={"name": call.name, "input": call.input, "result": call.result},
            )

        usage_dict = result.usage.model_dump()
        cost_usd = cost_for_usage(usage_dict, step.model)
        context.total_tokens += result.usage.total_tokens
        context.total_cost_usd += cost_usd
        self.metrics.agent_tokens(
            step.model,
            int(usage_dict.get("input_tokens", 0)),
            int(usage_dict.get("output_tokens", 0)),
        )
        self.metrics.bedrock_cost(step.model, cost_usd)

        return {
            "output_text": result.output_text,
            "stop_reason": result.stop_reason.value,
            "usage": usage_dict,
            "model": step.model,
            "cost_usd": cost_usd,
            "tool_calls": [c.model_dump() for c in result.tool_calls],
            "memory_hash": memory_hash,
        }

    # --- repository helpers ---

    async def _mark_instance(
        self,
        instance: WorkflowInstance,
        state: WorkflowInstanceState,
        context: WorkflowContext,
        *,
        error: str | None,
    ) -> WorkflowInstance:
        instance.state = state
        instance.context = context.model_dump()
        if state in (WorkflowInstanceState.COMPLETED, WorkflowInstanceState.FAILED):
            instance.completed_at = _utcnow()
        instance.error = error
        return await self.repositories.instances.update(instance)

    async def _audit(
        self,
        action: str,
        *,
        actor_type: str,
        actor_id: str,
        instance_id: str | None = None,
        step_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        entry = AuditEntry(
            id=_new_id(),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            workflow_instance_id=instance_id,
            step_id=step_id,
            detail=dict(detail or {}),
        )
        await self.repositories.audit.append(entry)
        if self.events is not None:
            await self.events.publish(entry.model_dump(mode="json"))


def _ancestors(definition: WorkflowDefinition, target_id: str) -> set[str]:
    """Topological ancestors of `target_id` — every step that must complete
    before `target_id` can run. Used by `fork` to pick the set of source-run
    step outputs to preserve."""
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in definition.edges:
        incoming[edge.target].append(edge.source)
    visited: set[str] = set()
    stack = [target_id]
    while stack:
        sid = stack.pop()
        for src in incoming.get(sid, []):
            if src not in visited:
                visited.add(src)
                stack.append(src)
    return visited


def _build_user_message(step: AgenticStep, context: WorkflowContext) -> str:
    """Compose the user message for an agentic step.

    Naive: states the goal, then dumps trigger payload + prior step outputs as
    JSON. Smarter context selection lands with knowledge retrieval (Phase B+).
    """
    payload = {"trigger": context.trigger, "prior_steps": context.steps}
    return f"{step.goal}\n\nContext:\n{json.dumps(payload, indent=2, default=str)}"
