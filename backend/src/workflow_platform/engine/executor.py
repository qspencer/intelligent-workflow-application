"""WorkflowEngine — sequential DAG executor.

Walks a `WorkflowDefinition`'s steps in topological order, dispatching each one
to the appropriate runner (deterministic = function from FunctionRegistry;
agentic = Agent with a per-step ToolRegistry). Records lifecycle in the
repositories and emits append-only audit entries for every state transition
and every agent tool call.

Phase 0 / Week 3 scope: sequential only. Parallel + conditional edges land in
Phase 1 (Week 5). Retries, timeouts, and pause/resume are also Phase 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from workflow_platform.agent import Agent, AgentPolicy
from workflow_platform.agent.registry import ToolRegistry as AgentToolRegistry
from workflow_platform.bedrock import BedrockClient
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.registry import FunctionRegistry, StepFailure
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.persistence.models import _new_id, _utcnow
from workflow_platform.tools import Tool, ToolContext
from workflow_platform.workflow import (
    AgenticStep,
    DeterministicStep,
    WorkflowDefinition,
    validate_and_order,
)
from workflow_platform.world import World


class ToolCatalog:
    """A name-keyed catalog of all tools available to agentic steps.

    Distinct from `ToolRegistry` (which is per-Agent and shapes Bedrock toolConfig).
    The engine builds a subset `ToolRegistry` per step from this catalog.
    """

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
class WorkflowEngine:
    repositories: Repositories
    functions: FunctionRegistry
    tools: ToolCatalog
    bedrock: BedrockClient
    world: World

    async def run(
        self,
        definition: WorkflowDefinition,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        ordered_step_ids = validate_and_order(definition)
        steps_by_id = {s.id: s for s in definition.steps}

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

        try:
            for step_id in ordered_step_ids:
                await self._run_step(steps_by_id[step_id], context, instance.id)
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

    async def _run_step(
        self,
        step: DeterministicStep | AgenticStep,
        context: WorkflowContext,
        instance_id: str,
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
            detail={"type": step.type},
        )

        try:
            if isinstance(step, DeterministicStep):
                output = await self._run_deterministic(step, context)
            else:
                output = await self._run_agentic(step, context, instance_id)
        except StepFailure as exc:
            execution.state = StepExecutionState.FAILED
            execution.error = str(exc)
            execution.completed_at = _utcnow()
            await self.repositories.steps.update(execution)
            await self._audit(
                "step_failed",
                actor_type="engine",
                actor_id=f"step:{step.id}",
                instance_id=instance_id,
                step_id=step.id,
                detail={"error": str(exc)},
            )
            raise

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
            detail={"output": output},
        )

    async def _run_deterministic(
        self, step: DeterministicStep, context: WorkflowContext
    ) -> dict[str, Any]:
        fn = self.functions.get(step.function)
        if fn is None:
            raise StepFailure(f"Unknown step function: {step.function!r}")
        return await fn(step.config, context, self.world)

    async def _run_agentic(
        self,
        step: AgenticStep,
        context: WorkflowContext,
        instance_id: str,
    ) -> dict[str, Any]:
        registry = AgentToolRegistry()
        for tool_name in step.tools:
            tool = self.tools.get(tool_name)
            if tool is None:
                raise StepFailure(f"Step {step.id!r} requires unknown tool {tool_name!r}")
            registry.register(tool)

        agent = Agent(
            system_prompt=step.system_prompt or step.goal,
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
        agent_id = f"agent:{step.id}"
        tool_ctx = ToolContext(
            world=self.world, agent_id=agent_id, workflow_instance_id=instance_id
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

    Naive for Week 3: states the goal, then dumps the trigger payload and prior
    step outputs as JSON. Smarter context selection (per LEARNING.md) lands in
    Phase 1+ once memory and retrieval are wired up.
    """
    payload = {"trigger": context.trigger, "prior_steps": context.steps}
    return f"{step.goal}\n\nContext:\n{json.dumps(payload, indent=2, default=str)}"
