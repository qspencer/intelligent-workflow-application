"""In-memory repository implementations for unit tests.

Same interface as the Postgres implementations. Stores everything in dicts /
lists scoped to the instance; constructing a fresh `in_memory_repositories()`
gives you a clean slate.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

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
from workflow_platform.workflow import WorkflowDefinition


class InMemoryDefinitionRepo(DefinitionRepo):
    def __init__(self) -> None:
        self._items: dict[str, WorkflowDefinition] = {}
        self._ownership: dict[str, tuple[str, str | None]] = {}

    async def save(
        self,
        definition: WorkflowDefinition,
        *,
        org_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> None:
        self._items[definition.id] = definition
        existing = self._ownership.get(definition.id, (DEFAULT_ORG_ID, None))
        self._ownership[definition.id] = (
            org_id or existing[0],
            owner_user_id or existing[1],
        )

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        return self._items.get(definition_id)

    async def list_all(self, org_id: str | None = None) -> list[WorkflowDefinition]:
        if org_id is None:
            return list(self._items.values())
        return [
            d
            for d in self._items.values()
            if self._ownership.get(d.id, (DEFAULT_ORG_ID, None))[0] == org_id
        ]

    async def org_of(self, definition_id: str) -> str | None:
        if definition_id not in self._items:
            return None
        return self._ownership.get(definition_id, (DEFAULT_ORG_ID, None))[0]

    async def delete(self, definition_id: str) -> bool:
        return self._items.pop(definition_id, None) is not None


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
        self, states: list[str], workflow_id: str | None = None, org_id: str | None = None
    ) -> list[str]:
        state_set = set(states)
        to_delete = [
            i.id
            for i in self._items.values()
            if i.state.value in state_set
            and (workflow_id is None or i.workflow_id == workflow_id)
            and (org_id is None or i.org_id == org_id)
        ]
        for iid in to_delete:
            del self._items[iid]
        return to_delete

    async def list_by_workflow(
        self, workflow_id: str, org_id: str | None = None
    ) -> list[WorkflowInstance]:
        return [
            i.model_copy(deep=True)
            for i in self._items.values()
            if i.workflow_id == workflow_id and (org_id is None or i.org_id == org_id)
        ]

    async def list_recent(
        self, limit: int = 1000, since: datetime | None = None, org_id: str | None = None
    ) -> list[WorkflowInstance]:
        items = list(self._items.values())
        if since is not None:
            items = [i for i in items if i.created_at >= since]
        if org_id is not None:
            items = [i for i in items if i.org_id == org_id]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [i.model_copy(deep=True) for i in items[: max(0, limit)]]

    async def count_by_workflow(self, org_id: str | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for inst in self._items.values():
            if org_id is not None and inst.org_id != org_id:
                continue
            out[inst.workflow_id] = out.get(inst.workflow_id, 0) + 1
        return out


class InMemoryStepExecutionRepo(StepExecutionRepo):
    def __init__(self, instances: InMemoryInstanceRepo | None = None) -> None:
        self._items: dict[str, StepExecution] = {}
        # For org scoping (ROLES_PLAN §4b join): steps carry no org; their
        # instance does.
        self._instances = instances

    def _org_of_instance(self, instance_id: str) -> str | None:
        if self._instances is None:
            return None
        item = self._instances._items.get(instance_id)
        return item.org_id if item else None

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
        self, limit: int = 1000, since: datetime | None = None, org_id: str | None = None
    ) -> list[StepExecution]:
        items = list(self._items.values())
        if since is not None:
            items = [e for e in items if e.started_at is not None and e.started_at >= since]
        if org_id is not None:
            items = [e for e in items if self._org_of_instance(e.instance_id) == org_id]
        items.sort(key=lambda e: e.started_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [e.model_copy(deep=True) for e in items[: max(0, limit)]]


class InMemoryAuditRepo(AuditRepo):
    def __init__(self, instances: InMemoryInstanceRepo | None = None) -> None:
        self._entries: list[AuditEntry] = []
        self._instances = instances

    def _org_of_instance(self, instance_id: str) -> str | None:
        if self._instances is None:
            return None
        item = self._instances._items.get(instance_id)
        return item.org_id if item else None

    async def append(self, entry: AuditEntry) -> AuditEntry:
        self._entries.append(entry.model_copy(deep=True))
        return self._entries[-1]

    async def list_recent(self, limit: int = 100, org_id: str | None = None) -> list[AuditEntry]:
        entries = self._entries
        if org_id is not None:
            # Instance-less (system) entries are platform-operator data —
            # excluded from org-scoped listings (ROLES_PLAN §4b).
            entries = [
                e
                for e in entries
                if e.workflow_instance_id is not None
                and self._org_of_instance(e.workflow_instance_id) == org_id
            ]
        return [deepcopy(e) for e in entries[-limit:]]

    async def list_by_instance(self, instance_id: str) -> list[AuditEntry]:
        return [deepcopy(e) for e in self._entries if e.workflow_instance_id == instance_id]


class InMemoryTriggerCursorRepo(TriggerCursorRepo):
    def __init__(self) -> None:
        self._states: dict[str, TriggerCursorState] = {}

    async def get(self, trigger_id: str) -> TriggerCursorState | None:
        state = self._states.get(trigger_id)
        return state.model_copy(deep=True) if state else None

    async def set(self, trigger_id: str, state: TriggerCursorState) -> None:
        self._states[trigger_id] = state.model_copy(deep=True)


class InMemoryOrganizationRepo(OrganizationRepo):
    def __init__(self) -> None:
        self._items: dict[str, Organization] = {
            DEFAULT_ORG_ID: Organization(id=DEFAULT_ORG_ID, name="default")
        }

    async def get(self, org_id: str) -> Organization | None:
        org = self._items.get(org_id)
        return org.model_copy(deep=True) if org else None

    async def save(self, org: Organization) -> Organization:
        self._items[org.id] = org.model_copy(deep=True)
        return org


class InMemoryUserRepo(UserRepo):
    def __init__(self) -> None:
        self._items: dict[str, User] = {}

    async def get(self, user_id: str) -> User | None:
        user = self._items.get(user_id)
        return user.model_copy(deep=True) if user else None

    async def get_by_identity(self, iss: str, sub: str) -> User | None:
        for user in self._items.values():
            if user.iss == iss and user.sub == sub:
                return user.model_copy(deep=True)
        return None

    async def upsert_seen(self, user: User) -> User:
        existing = await self.get_by_identity(user.iss, user.sub)
        if existing is None:
            self._items[user.id] = user.model_copy(deep=True)
            return user
        existing.email = user.email or existing.email
        existing.display_name = user.display_name or existing.display_name
        existing.last_seen_at = user.last_seen_at
        self._items[existing.id] = existing.model_copy(deep=True)
        return existing

    async def get_by_login_email(self, email: str) -> User | None:
        canonical = email.strip().lower()
        for user in self._items.values():
            if user.password_hash and (user.email or "").lower() == canonical:
                return user.model_copy(deep=True)
        return None

    async def list_all(self) -> list[User]:
        return [u.model_copy(deep=True) for u in self._items.values()]

    async def save(self, user: User) -> User:
        self._items[user.id] = user.model_copy(deep=True)
        return user


class InMemoryAuthSessionRepo(AuthSessionRepo):
    def __init__(self) -> None:
        self._items: dict[str, AuthSession] = {}

    async def create(self, session: AuthSession) -> AuthSession:
        self._items[session.id] = session.model_copy(deep=True)
        return session

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        for session in self._items.values():
            if session.token_hash == token_hash:
                return session.model_copy(deep=True)
        return None

    async def update(self, session: AuthSession) -> AuthSession:
        self._items[session.id] = session.model_copy(deep=True)
        return session

    async def delete_by_token_hash(self, token_hash: str) -> bool:
        for sid, session in list(self._items.items()):
            if session.token_hash == token_hash:
                del self._items[sid]
                return True
        return False

    async def delete_by_user(self, user_id: str) -> int:
        doomed = [sid for sid, s in self._items.items() if s.user_id == user_id]
        for sid in doomed:
            del self._items[sid]
        return len(doomed)


def in_memory_repositories() -> Repositories:
    instances = InMemoryInstanceRepo()
    return Repositories(
        definitions=InMemoryDefinitionRepo(),
        instances=instances,
        steps=InMemoryStepExecutionRepo(instances),
        audit=InMemoryAuditRepo(instances),
        trigger_cursors=InMemoryTriggerCursorRepo(),
        organizations=InMemoryOrganizationRepo(),
        users=InMemoryUserRepo(),
        auth_sessions=InMemoryAuthSessionRepo(),
    )
