"""Repository interfaces — the persistence boundary.

The engine writes through these interfaces. An in-memory implementation
(`memory.py`) backs unit tests; a Postgres implementation lands later in Week 3
and serves production. Tests should never see SQL; the engine never sees
asyncpg.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from workflow_platform.persistence.models import (
    AuditEntry,
    StepExecution,
    WorkflowInstance,
)
from workflow_platform.workflow import WorkflowDefinition


class DefinitionRepo(ABC):
    @abstractmethod
    async def save(self, definition: WorkflowDefinition) -> None: ...

    @abstractmethod
    async def get(self, definition_id: str) -> WorkflowDefinition | None: ...

    @abstractmethod
    async def list_all(self) -> list[WorkflowDefinition]: ...


class InstanceRepo(ABC):
    @abstractmethod
    async def create(self, instance: WorkflowInstance) -> WorkflowInstance: ...

    @abstractmethod
    async def get(self, instance_id: str) -> WorkflowInstance | None: ...

    @abstractmethod
    async def update(self, instance: WorkflowInstance) -> WorkflowInstance: ...

    @abstractmethod
    async def delete(self, instance_id: str) -> bool:
        """Hard-delete an instance row. Returns True if a row was deleted,
        False if no instance existed. Callers are responsible for also
        deleting related step_executions; audit entries are intentionally
        left intact so the historical record remains tamper-evident."""

    @abstractmethod
    async def delete_by_states(
        self, states: list[str], workflow_id: str | None = None
    ) -> list[str]:
        """Bulk hard-delete instances in any of the given states. Optionally
        scope to one workflow_id. Returns the deleted instance IDs so
        callers can cascade step_execution deletes. Audit entries left
        intact, same as single-instance delete."""

    @abstractmethod
    async def list_by_workflow(self, workflow_id: str) -> list[WorkflowInstance]: ...

    @abstractmethod
    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[WorkflowInstance]:
        """System-wide list of instances ordered by created_at DESC."""

    @abstractmethod
    async def count_by_workflow(self) -> dict[str, int]:
        """Return a `{workflow_id: instance_count}` map across all instances.
        Used by the workflows list page to show how many runs each
        definition has accumulated."""


class StepExecutionRepo(ABC):
    @abstractmethod
    async def create(self, execution: StepExecution) -> StepExecution: ...

    @abstractmethod
    async def update(self, execution: StepExecution) -> StepExecution: ...

    @abstractmethod
    async def delete_by_instance(self, instance_id: str) -> int:
        """Delete every step_execution row tied to one instance. Returns
        the number of rows deleted. Called as part of instance hard-delete."""

    @abstractmethod
    async def delete_by_instances(self, instance_ids: list[str]) -> int:
        """Bulk version of `delete_by_instance`. Deletes every step_execution
        whose `instance_id` is in the given list. Returns the total row
        count deleted."""

    @abstractmethod
    async def list_by_instance(self, instance_id: str) -> list[StepExecution]: ...

    @abstractmethod
    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[StepExecution]:
        """Recent step executions, newest first (by started_at)."""


class AuditRepo(ABC):
    @abstractmethod
    async def append(self, entry: AuditEntry) -> AuditEntry: ...

    @abstractmethod
    async def list_recent(self, limit: int = 100) -> list[AuditEntry]: ...

    @abstractmethod
    async def list_by_instance(self, instance_id: str) -> list[AuditEntry]: ...


@dataclass
class Repositories:
    """Bundle of repository implementations the engine takes."""

    definitions: DefinitionRepo
    instances: InstanceRepo
    steps: StepExecutionRepo
    audit: AuditRepo
