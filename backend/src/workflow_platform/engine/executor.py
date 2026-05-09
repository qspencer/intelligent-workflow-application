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
import json
import logging
from collections import defaultdict
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

import simpleeval

from workflow_platform.agent import Agent, AgentPolicy
from workflow_platform.agent.registry import ToolRegistry as AgentToolRegistry
from workflow_platform.bedrock import BedrockClient
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.registry import FunctionRegistry, StepFailure
from workflow_platform.memory import MemoryManager
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


@dataclass
class WorkflowEngine:
    repositories: Repositories
    functions: FunctionRegistry
    tools: ToolCatalog
    bedrock: BedrockClient
    world: World
    system_capabilities: CapabilityPolicy | None = None
    memory: MemoryManager | None = None
    pause_check_interval: float = 0.0  # seconds; 0 disables polling-based pause checks

    # --- public API ---

    async def run(
        self,
        definition: WorkflowDefinition,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        validate_and_order(definition)

        instance = WorkflowInstance(
            workflow_id=definition.id,
            state=WorkflowInstanceState.RUNNING,
            trigger_payload=dict(trigger_payload or {}),
            started_at=_utcnow(),
        )
        instance = await self.repositories.instances.create(instance)
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
        return await self._drive(definition, instance, context, already_done=set())

    async def resume(self, definition: WorkflowDefinition, instance_id: str) -> WorkflowInstance:
        """Resume a paused instance. Replays no completed work; picks up where
        the previous run left off."""
        instance = await self.repositories.instances.get(instance_id)
        if instance is None:
            raise ValueError(f"Instance {instance_id} not found")
        if instance.state != WorkflowInstanceState.PAUSED:
            raise ValueError(f"Instance {instance_id} is {instance.state.value}, cannot resume")

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
        return await self._drive(definition, instance, context, already_done=already_done)

    # --- core dispatch loop ---

    async def _drive(
        self,
        definition: WorkflowDefinition,
        instance: WorkflowInstance,
        context: WorkflowContext,
        *,
        already_done: set[str],
    ) -> WorkflowInstance:
        steps_by_id = {s.id: s for s in definition.steps}
        state = self._build_dag_state(definition, already_done)

        try:
            timeout = definition.policies.timeout_seconds
            if timeout:
                await asyncio.wait_for(
                    self._dispatch_loop(definition, steps_by_id, state, context, instance.id),
                    timeout=timeout,
                )
            else:
                await self._dispatch_loop(definition, steps_by_id, state, context, instance.id)
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
    ) -> None:
        in_progress: dict[str, asyncio.Task[dict[str, Any] | None]] = {}
        scheduled: set[str] = set()
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

                # Step succeeded; resolve outgoing edges, dispatch newly-ready dependents.
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

    async def _maybe_pause(
        self, instance_id: str, in_progress: dict[str, asyncio.Task[Any]]
    ) -> None:
        fresh = await self.repositories.instances.get(instance_id)
        if fresh is not None and fresh.state == WorkflowInstanceState.PAUSED:
            await self._cancel_pending(in_progress)
            raise _PauseRequested()

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
        execution = StepExecution(
            instance_id=instance_id,
            step_id=step.id,
            state=StepExecutionState.RUNNING,
            started_at=_utcnow(),
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
            execution.completed_at = _utcnow()
            await self.repositories.steps.update(execution)
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
        execution.completed_at = _utcnow()
        await self.repositories.steps.update(execution)
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

        return {
            "output_text": result.output_text,
            "stop_reason": result.stop_reason.value,
            "usage": result.usage.model_dump(),
            "tool_calls": [c.model_dump() for c in result.tool_calls],
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


def _build_user_message(step: AgenticStep, context: WorkflowContext) -> str:
    """Compose the user message for an agentic step.

    Naive: states the goal, then dumps trigger payload + prior step outputs as
    JSON. Smarter context selection lands with knowledge retrieval (Phase B+).
    """
    payload = {"trigger": context.trigger, "prior_steps": context.steps}
    return f"{step.goal}\n\nContext:\n{json.dumps(payload, indent=2, default=str)}"
