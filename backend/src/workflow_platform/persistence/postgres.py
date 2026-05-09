"""Postgres-backed repository implementations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from workflow_platform.persistence.sqlalchemy_models import (
    AuditLogRow,
    StepExecutionRow,
    WorkflowDefinitionRow,
    WorkflowInstanceRow,
)
from workflow_platform.workflow import WorkflowDefinition


class PostgresDefinitionRepo(DefinitionRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save(self, definition: WorkflowDefinition) -> None:
        async with self._sf() as s, s.begin():
            existing = await s.get(WorkflowDefinitionRow, definition.id)
            body = definition.model_dump(by_alias=True)
            if existing is None:
                s.add(
                    WorkflowDefinitionRow(
                        id=definition.id,
                        name=definition.name,
                        description=definition.description,
                        body=body,
                    )
                )
            else:
                existing.name = definition.name
                existing.description = definition.description
                existing.body = body

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        async with self._sf() as s:
            row = await s.get(WorkflowDefinitionRow, definition_id)
        return WorkflowDefinition.model_validate(row.body) if row else None

    async def list_all(self) -> list[WorkflowDefinition]:
        async with self._sf() as s:
            result = await s.execute(select(WorkflowDefinitionRow))
            rows = result.scalars().all()
        return [WorkflowDefinition.model_validate(r.body) for r in rows]


class PostgresInstanceRepo(InstanceRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, instance: WorkflowInstance) -> WorkflowInstance:
        async with self._sf() as s, s.begin():
            s.add(_to_instance_row(instance))
        return instance

    async def get(self, instance_id: str) -> WorkflowInstance | None:
        async with self._sf() as s:
            row = await s.get(WorkflowInstanceRow, instance_id)
        return _from_instance_row(row) if row else None

    async def update(self, instance: WorkflowInstance) -> WorkflowInstance:
        async with self._sf() as s, s.begin():
            row = await s.get(WorkflowInstanceRow, instance.id)
            if row is None:
                raise ValueError(f"Instance {instance.id} not found")
            row.state = instance.state.value
            row.trigger_payload = instance.trigger_payload
            row.context = instance.context
            row.error = instance.error
            row.started_at = instance.started_at
            row.completed_at = instance.completed_at
        return instance

    async def list_by_workflow(self, workflow_id: str) -> list[WorkflowInstance]:
        async with self._sf() as s:
            result = await s.execute(
                select(WorkflowInstanceRow).where(WorkflowInstanceRow.workflow_id == workflow_id)
            )
            rows = result.scalars().all()
        return [_from_instance_row(r) for r in rows]


class PostgresStepExecutionRepo(StepExecutionRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, execution: StepExecution) -> StepExecution:
        async with self._sf() as s, s.begin():
            s.add(_to_step_row(execution))
        return execution

    async def update(self, execution: StepExecution) -> StepExecution:
        async with self._sf() as s, s.begin():
            row = await s.get(StepExecutionRow, execution.id)
            if row is None:
                raise ValueError(f"StepExecution {execution.id} not found")
            row.state = execution.state.value
            row.output = execution.output
            row.error = execution.error
            row.started_at = execution.started_at
            row.completed_at = execution.completed_at
        return execution

    async def list_by_instance(self, instance_id: str) -> list[StepExecution]:
        async with self._sf() as s:
            result = await s.execute(
                select(StepExecutionRow)
                .where(StepExecutionRow.instance_id == instance_id)
                .order_by(StepExecutionRow.started_at.asc().nullsfirst())
            )
            rows = result.scalars().all()
        return [_from_step_row(r) for r in rows]


class PostgresAuditRepo(AuditRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def append(self, entry: AuditEntry) -> AuditEntry:
        async with self._sf() as s, s.begin():
            s.add(
                AuditLogRow(
                    id=entry.id,
                    timestamp=entry.timestamp,
                    actor_type=entry.actor_type,
                    actor_id=entry.actor_id,
                    action=entry.action,
                    workflow_instance_id=entry.workflow_instance_id,
                    step_id=entry.step_id,
                    detail=entry.detail,
                )
            )
        return entry

    async def list_recent(self, limit: int = 100) -> list[AuditEntry]:
        async with self._sf() as s:
            result = await s.execute(
                select(AuditLogRow).order_by(AuditLogRow.timestamp.desc()).limit(limit)
            )
            rows = list(result.scalars().all())
        return list(reversed([_from_audit_row(r) for r in rows]))

    async def list_by_instance(self, instance_id: str) -> list[AuditEntry]:
        async with self._sf() as s:
            result = await s.execute(
                select(AuditLogRow)
                .where(AuditLogRow.workflow_instance_id == instance_id)
                .order_by(AuditLogRow.timestamp.asc())
            )
            rows = result.scalars().all()
        return [_from_audit_row(r) for r in rows]


def postgres_repositories(session_factory: async_sessionmaker[AsyncSession]) -> Repositories:
    return Repositories(
        definitions=PostgresDefinitionRepo(session_factory),
        instances=PostgresInstanceRepo(session_factory),
        steps=PostgresStepExecutionRepo(session_factory),
        audit=PostgresAuditRepo(session_factory),
    )


# --- conversion helpers ---


def _to_instance_row(instance: WorkflowInstance) -> WorkflowInstanceRow:
    return WorkflowInstanceRow(
        id=instance.id,
        workflow_id=instance.workflow_id,
        state=instance.state.value,
        trigger_payload=instance.trigger_payload,
        context=instance.context,
        error=instance.error,
        created_at=instance.created_at,
        started_at=instance.started_at,
        completed_at=instance.completed_at,
    )


def _from_instance_row(row: WorkflowInstanceRow) -> WorkflowInstance:
    return WorkflowInstance(
        id=row.id,
        workflow_id=row.workflow_id,
        state=row.state,
        trigger_payload=_as_dict(row.trigger_payload),
        context=_as_dict(row.context),
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _to_step_row(execution: StepExecution) -> StepExecutionRow:
    return StepExecutionRow(
        id=execution.id,
        instance_id=execution.instance_id,
        step_id=execution.step_id,
        state=execution.state.value,
        output=execution.output,
        error=execution.error,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )


def _from_step_row(row: StepExecutionRow) -> StepExecution:
    return StepExecution(
        id=row.id,
        instance_id=row.instance_id,
        step_id=row.step_id,
        state=row.state,
        output=_as_dict(row.output) if row.output is not None else None,
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _from_audit_row(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        timestamp=row.timestamp,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        action=row.action,
        workflow_instance_id=row.workflow_instance_id,
        step_id=row.step_id,
        detail=_as_dict(row.detail),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
