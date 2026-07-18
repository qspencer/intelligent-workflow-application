"""Postgres-backed repository implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workflow_platform.persistence.models import (
    DEFAULT_ORG_ID,
    AuditEntry,
    AuthSession,
    Organization,
    StepExecution,
    TriggerCursorState,
    User,
    WorkflowInstance,
)
from workflow_platform.persistence.repository import (
    AuditRepo,
    AuthSessionRepo,
    DefinitionRepo,
    InstanceRepo,
    OrganizationRepo,
    Repositories,
    StepExecutionRepo,
    TriggerCursorRepo,
    UserRepo,
)
from workflow_platform.persistence.sqlalchemy_models import (
    AuditLogRow,
    AuthSessionRow,
    OrganizationRow,
    StepExecutionRow,
    TriggerCursorRow,
    UserRow,
    WorkflowDefinitionRow,
    WorkflowInstanceRow,
)
from workflow_platform.workflow import WorkflowDefinition


class PostgresDefinitionRepo(DefinitionRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save(
        self,
        definition: WorkflowDefinition,
        *,
        org_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> None:
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
                        org_id=org_id or DEFAULT_ORG_ID,
                        owner_user_id=owner_user_id,
                    )
                )
            else:
                existing.name = definition.name
                existing.description = definition.description
                existing.body = body
                if org_id is not None:
                    existing.org_id = org_id
                if owner_user_id is not None:
                    existing.owner_user_id = owner_user_id

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        async with self._sf() as s:
            row = await s.get(WorkflowDefinitionRow, definition_id)
        return WorkflowDefinition.model_validate(row.body) if row else None

    async def list_all(self) -> list[WorkflowDefinition]:
        async with self._sf() as s:
            result = await s.execute(select(WorkflowDefinitionRow))
            rows = result.scalars().all()
        return [WorkflowDefinition.model_validate(r.body) for r in rows]

    async def delete(self, definition_id: str) -> bool:
        async with self._sf() as s, s.begin():
            row = await s.get(WorkflowDefinitionRow, definition_id)
            if row is None:
                return False
            await s.delete(row)
        return True


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

    async def delete(self, instance_id: str) -> bool:
        async with self._sf() as s, s.begin():
            row = await s.get(WorkflowInstanceRow, instance_id)
            if row is None:
                return False
            await s.delete(row)
        return True

    async def delete_by_states(
        self, states: list[str], workflow_id: str | None = None
    ) -> list[str]:
        async with self._sf() as s, s.begin():
            id_stmt = select(WorkflowInstanceRow.id).where(WorkflowInstanceRow.state.in_(states))
            if workflow_id is not None:
                id_stmt = id_stmt.where(WorkflowInstanceRow.workflow_id == workflow_id)
            result = await s.execute(id_stmt)
            ids = [row[0] for row in result.all()]
            if ids:
                await s.execute(
                    sql_delete(WorkflowInstanceRow).where(WorkflowInstanceRow.id.in_(ids))
                )
        return ids

    async def list_by_workflow(self, workflow_id: str) -> list[WorkflowInstance]:
        async with self._sf() as s:
            result = await s.execute(
                select(WorkflowInstanceRow).where(WorkflowInstanceRow.workflow_id == workflow_id)
            )
            rows = result.scalars().all()
        return [_from_instance_row(r) for r in rows]

    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[WorkflowInstance]:
        async with self._sf() as s:
            stmt = select(WorkflowInstanceRow).order_by(WorkflowInstanceRow.created_at.desc())
            if since is not None:
                stmt = stmt.where(WorkflowInstanceRow.created_at >= since)
            stmt = stmt.limit(max(0, limit))
            result = await s.execute(stmt)
            rows = result.scalars().all()
        return [_from_instance_row(r) for r in rows]

    async def count_by_workflow(self) -> dict[str, int]:
        async with self._sf() as s:
            stmt = select(
                WorkflowInstanceRow.workflow_id, func.count(WorkflowInstanceRow.id)
            ).group_by(WorkflowInstanceRow.workflow_id)
            result = await s.execute(stmt)
            return {row[0]: int(row[1]) for row in result.all()}


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

    async def delete_by_instance(self, instance_id: str) -> int:
        async with self._sf() as s, s.begin():
            result = await s.execute(
                sql_delete(StepExecutionRow).where(StepExecutionRow.instance_id == instance_id)
            )
        # `Result.rowcount` is typed loose in SQLAlchemy 2.0 stubs but is a
        # real attribute on the CursorResult returned for DML statements.
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount

    async def delete_by_instances(self, instance_ids: list[str]) -> int:
        if not instance_ids:
            return 0
        async with self._sf() as s, s.begin():
            result = await s.execute(
                sql_delete(StepExecutionRow).where(StepExecutionRow.instance_id.in_(instance_ids))
            )
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount

    async def list_by_instance(self, instance_id: str) -> list[StepExecution]:
        async with self._sf() as s:
            result = await s.execute(
                select(StepExecutionRow)
                .where(StepExecutionRow.instance_id == instance_id)
                .order_by(StepExecutionRow.started_at.asc().nullsfirst())
            )
            rows = result.scalars().all()
        return [_from_step_row(r) for r in rows]

    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None
    ) -> list[StepExecution]:
        async with self._sf() as s:
            stmt = select(StepExecutionRow).order_by(StepExecutionRow.started_at.desc().nullslast())
            if since is not None:
                stmt = stmt.where(StepExecutionRow.started_at >= since)
            stmt = stmt.limit(max(0, limit))
            result = await s.execute(stmt)
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


class PostgresTriggerCursorRepo(TriggerCursorRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, trigger_id: str) -> TriggerCursorState | None:
        async with self._sf() as s:
            row = await s.get(TriggerCursorRow, trigger_id)
            if row is None:
                return None
            return TriggerCursorState(
                cursor=row.cursor, seen_ids=list(row.seen_ids), updated_at=row.updated_at
            )

    async def set(self, trigger_id: str, state: TriggerCursorState) -> None:
        async with self._sf() as s, s.begin():
            stmt = pg_insert(TriggerCursorRow).values(
                trigger_id=trigger_id,
                cursor=state.cursor,
                seen_ids=state.seen_ids,
                updated_at=state.updated_at,
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[TriggerCursorRow.trigger_id],
                    set_={
                        "cursor": stmt.excluded.cursor,
                        "seen_ids": stmt.excluded.seen_ids,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
            )


class PostgresOrganizationRepo(OrganizationRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, org_id: str) -> Organization | None:
        async with self._sf() as s:
            row = await s.get(OrganizationRow, org_id)
            if row is None:
                return None
            return Organization(id=row.id, name=row.name, created_at=row.created_at)

    async def save(self, org: Organization) -> Organization:
        async with self._sf() as s, s.begin():
            existing = await s.get(OrganizationRow, org.id)
            if existing is None:
                s.add(OrganizationRow(id=org.id, name=org.name, created_at=org.created_at))
            else:
                existing.name = org.name
        return org


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        iss=row.iss,
        sub=row.sub,
        email=row.email,
        display_name=row.display_name,
        org_id=row.org_id,
        password_hash=row.password_hash,
        roles=list(row.roles or []),
        is_active=row.is_active,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


def _row_to_auth_session(row: AuthSessionRow) -> AuthSession:
    return AuthSession(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_seen_at=row.last_seen_at,
    )


class PostgresUserRepo(UserRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, user_id: str) -> User | None:
        async with self._sf() as s:
            row = await s.get(UserRow, user_id)
            return _row_to_user(row) if row else None

    async def get_by_identity(self, iss: str, sub: str) -> User | None:
        async with self._sf() as s:
            result = await s.execute(select(UserRow).where(UserRow.iss == iss, UserRow.sub == sub))
            row = result.scalar_one_or_none()
            return _row_to_user(row) if row else None

    async def upsert_seen(self, user: User) -> User:
        async with self._sf() as s, s.begin():
            stmt = pg_insert(UserRow).values(
                id=user.id,
                iss=user.iss,
                sub=user.sub,
                email=user.email,
                display_name=user.display_name,
                org_id=user.org_id,
                created_at=user.created_at,
                last_seen_at=user.last_seen_at,
            )
            # First sight inserts; a known (iss, sub) refreshes contact fields
            # and last_seen_at but keeps its original id/org/created_at.
            await s.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_users_iss_sub",
                    set_={
                        "email": stmt.excluded.email,
                        "display_name": stmt.excluded.display_name,
                        "last_seen_at": stmt.excluded.last_seen_at,
                    },
                )
            )
        stored = await self.get_by_identity(user.iss, user.sub)
        assert stored is not None  # just upserted
        return stored

    async def get_by_login_email(self, email: str) -> User | None:
        canonical = email.strip().lower()
        async with self._sf() as s:
            result = await s.execute(
                select(UserRow).where(
                    func.lower(UserRow.email) == canonical,
                    UserRow.password_hash.is_not(None),
                )
            )
            row = result.scalar_one_or_none()
            return _row_to_user(row) if row else None

    async def list_all(self) -> list[User]:
        async with self._sf() as s:
            result = await s.execute(select(UserRow).order_by(UserRow.created_at))
            return [_row_to_user(r) for r in result.scalars()]

    async def save(self, user: User) -> User:
        async with self._sf() as s, s.begin():
            row = await s.get(UserRow, user.id)
            if row is None:
                row = UserRow(id=user.id)
                s.add(row)
            row.iss = user.iss
            row.sub = user.sub
            row.email = user.email
            row.display_name = user.display_name
            row.org_id = user.org_id
            row.password_hash = user.password_hash
            row.roles = list(user.roles)
            row.is_active = user.is_active
            row.created_at = user.created_at
            row.last_seen_at = user.last_seen_at
        return user


class PostgresAuthSessionRepo(AuthSessionRepo):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, session: AuthSession) -> AuthSession:
        async with self._sf() as s, s.begin():
            s.add(
                AuthSessionRow(
                    id=session.id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    last_seen_at=session.last_seen_at,
                )
            )
        return session

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        async with self._sf() as s:
            result = await s.execute(
                select(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash)
            )
            row = result.scalar_one_or_none()
            return _row_to_auth_session(row) if row else None

    async def update(self, session: AuthSession) -> AuthSession:
        async with self._sf() as s, s.begin():
            row = await s.get(AuthSessionRow, session.id)
            if row is not None:
                row.expires_at = session.expires_at
                row.last_seen_at = session.last_seen_at
        return session

    async def delete_by_token_hash(self, token_hash: str) -> bool:
        async with self._sf() as s, s.begin():
            result = await s.execute(
                sql_delete(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash)
            )
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount > 0

    async def delete_by_user(self, user_id: str) -> int:
        async with self._sf() as s, s.begin():
            result = await s.execute(
                sql_delete(AuthSessionRow).where(AuthSessionRow.user_id == user_id)
            )
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount


def postgres_repositories(session_factory: async_sessionmaker[AsyncSession]) -> Repositories:
    return Repositories(
        definitions=PostgresDefinitionRepo(session_factory),
        instances=PostgresInstanceRepo(session_factory),
        steps=PostgresStepExecutionRepo(session_factory),
        audit=PostgresAuditRepo(session_factory),
        trigger_cursors=PostgresTriggerCursorRepo(session_factory),
        organizations=PostgresOrganizationRepo(session_factory),
        users=PostgresUserRepo(session_factory),
        auth_sessions=PostgresAuthSessionRepo(session_factory),
    )


# --- conversion helpers ---


def _to_instance_row(instance: WorkflowInstance) -> WorkflowInstanceRow:
    return WorkflowInstanceRow(
        id=instance.id,
        workflow_id=instance.workflow_id,
        org_id=instance.org_id,
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
        org_id=row.org_id,
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
