"""In-memory repository implementations for unit tests.

Same interface as the Postgres implementations. Stores everything in dicts /
lists scoped to the instance; constructing a fresh `in_memory_repositories()`
gives you a clean slate.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from workflow_platform.persistence.models import (
    AuditEntry,
    StepExecution,
    WorkflowInstance,
)
from workflow_platform.persistence.repository import (
    AuditRepo,
    DefinitionRepo,
    InstanceRepo,
    Repositories,
    StepExecutionRepo,
)
from workflow_platform.workflow import WorkflowDefinition


class InMemoryDefinitionRepo(DefinitionRepo):
    def __init__(self) -> None:
        self._items: dict[str, WorkflowDefinition] = {}

    async def save(self, definition: WorkflowDefinition) -> None:
        self._items[definition.id] = definition

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        return self._items.get(definition_id)

    async def list_all(self) -> list[WorkflowDefinition]:
        return list(self._items.values())


class InMemoryInstanceRepo(InstanceRepo):
    def __init__(self) -> None:
        self._items: dict[str, WorkflowInstance] = {}

    async def create(self, instance: WorkflowInstance) -> WorkflowInstance:
        if instance.id in self._items:
            raise ValueError(f"Instance {instance.id} already exists")
        self._items[instance.id] = instance.model_copy(deep=True)
        return self._items[instance.id]

    async def get(self, instance_id: str) -> WorkflowInstance | None:
        item = self._items.get(instance_id)
        return item.model_copy(deep=True) if item else None

    async def update(self, instance: WorkflowInstance) -> WorkflowInstance:
        if instance.id not in self._items:
            raise ValueError(f"Instance {instance.id} not found")
        self._items[instance.id] = instance.model_copy(deep=True)
        return self._items[instance.id]

    async def delete(self, instance_id: str) -> bool:
        return self._items.pop(instance_id, None) is not None

    async def delete_by_states(
        self, states: list[str], workflow_id: str | None = None
    ) -> list[str]:
        state_set = set(states)
        to_delete = [
            i.id
            for i in self._items.values()
            if i.state.value in state_set
            and (workflow_id is None or i.workflow_id == workflow_id)
        ]
        for iid in to_delete:
            del self._items[iid]
        return to_delete

    async def list_by_workflow(self, workflow_id: str) -> list[WorkflowInstance]:
        return [
            i.model_copy(deep=True) for i in self._items.values() if i.workflow_id == workflow_id
        ]

    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[WorkflowInstance]:
        items = list(self._items.values())
        if since is not None:
            items = [i for i in items if i.created_at >= since]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [i.model_copy(deep=True) for i in items[: max(0, limit)]]


class InMemoryStepExecutionRepo(StepExecutionRepo):
    def __init__(self) -> None:
        self._items: dict[str, StepExecution] = {}

    async def create(self, execution: StepExecution) -> StepExecution:
        if execution.id in self._items:
            raise ValueError(f"StepExecution {execution.id} already exists")
        self._items[execution.id] = execution.model_copy(deep=True)
        return self._items[execution.id]

    async def update(self, execution: StepExecution) -> StepExecution:
        if execution.id not in self._items:
            raise ValueError(f"StepExecution {execution.id} not found")
        self._items[execution.id] = execution.model_copy(deep=True)
        return self._items[execution.id]

    async def delete_by_instance(self, instance_id: str) -> int:
        to_remove = [eid for eid, e in self._items.items() if e.instance_id == instance_id]
        for eid in to_remove:
            del self._items[eid]
        return len(to_remove)

    async def delete_by_instances(self, instance_ids: list[str]) -> int:
        target = set(instance_ids)
        if not target:
            return 0
        to_remove = [eid for eid, e in self._items.items() if e.instance_id in target]
        for eid in to_remove:
            del self._items[eid]
        return len(to_remove)

    async def list_by_instance(self, instance_id: str) -> list[StepExecution]:
        return [
            e.model_copy(deep=True) for e in self._items.values() if e.instance_id == instance_id
        ]

    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[StepExecution]:
        items = list(self._items.values())
        if since is not None:
            items = [e for e in items if e.started_at is not None and e.started_at >= since]
        items.sort(key=lambda e: e.started_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [e.model_copy(deep=True) for e in items[: max(0, limit)]]


class InMemoryAuditRepo(AuditRepo):
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    async def append(self, entry: AuditEntry) -> AuditEntry:
        self._entries.append(entry.model_copy(deep=True))
        return self._entries[-1]

    async def list_recent(self, limit: int = 100) -> list[AuditEntry]:
        return [deepcopy(e) for e in self._entries[-limit:]]

    async def list_by_instance(self, instance_id: str) -> list[AuditEntry]:
        return [deepcopy(e) for e in self._entries if e.workflow_instance_id == instance_id]


def in_memory_repositories() -> Repositories:
    return Repositories(
        definitions=InMemoryDefinitionRepo(),
        instances=InMemoryInstanceRepo(),
        steps=InMemoryStepExecutionRepo(),
        audit=InMemoryAuditRepo(),
    )
