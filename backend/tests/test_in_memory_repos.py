"""Sanity tests for the in-memory repository implementations."""

from __future__ import annotations

from workflow_platform.persistence import (
    AuditEntry,
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition


async def test_definition_save_get_list() -> None:
    repos = in_memory_repositories()
    definition = load_definition(
        {
            "id": "wf-1",
            "name": "First",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
            "edges": [],
        }
    )
    await repos.definitions.save(definition)
    fetched = await repos.definitions.get("wf-1")
    assert fetched is not None
    assert fetched.name == "First"
    assert (await repos.definitions.list_all())[0].id == "wf-1"


async def test_instance_lifecycle() -> None:
    repos = in_memory_repositories()
    instance = WorkflowInstance(workflow_id="wf-1")
    created = await repos.instances.create(instance)
    assert created.state == WorkflowInstanceState.PENDING

    instance.state = WorkflowInstanceState.RUNNING
    await repos.instances.update(instance)
    fetched = await repos.instances.get(instance.id)
    assert fetched is not None
    assert fetched.state == WorkflowInstanceState.RUNNING


async def test_step_execution_lifecycle() -> None:
    repos = in_memory_repositories()
    execution = StepExecution(instance_id="i-1", step_id="a")
    await repos.steps.create(execution)
    execution.state = StepExecutionState.COMPLETED
    execution.output = {"value": 1}
    await repos.steps.update(execution)
    rows = await repos.steps.list_by_instance("i-1")
    assert len(rows) == 1
    assert rows[0].state == StepExecutionState.COMPLETED
    assert rows[0].output == {"value": 1}


async def test_audit_append_only_and_filtering() -> None:
    repos = in_memory_repositories()
    for action in ["workflow_started", "step_started", "step_completed", "workflow_completed"]:
        await repos.audit.append(
            AuditEntry(
                actor_type="engine",
                actor_id="x",
                action=action,
                workflow_instance_id="i-1",
            )
        )
    await repos.audit.append(
        AuditEntry(actor_type="engine", actor_id="y", action="other", workflow_instance_id="i-2")
    )
    by_instance = await repos.audit.list_by_instance("i-1")
    assert [e.action for e in by_instance] == [
        "workflow_started",
        "step_started",
        "step_completed",
        "workflow_completed",
    ]
    recent = await repos.audit.list_recent(limit=2)
    assert len(recent) == 2
