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
    async def list_by_workflow(self, workflow_id: str) -> list[WorkflowInstance]: ...


class StepExecutionRepo(ABC):
    @abstractmethod
    async def create(self, execution: StepExecution) -> StepExecution: ...

    @abstractmethod
    async def update(self, execution: StepExecution) -> StepExecution: ...

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
