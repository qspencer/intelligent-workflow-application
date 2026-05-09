"""HTTP routes for workflow definitions, instances, and the audit log.

Phase 1 / Week 4: read-only listing/inspection plus role-gated access.

Role gating (per `docs/ARCHITECTURE.md` D4):
- Read endpoints: any authenticated role.
- Audit endpoints: Admin or Auditor only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from workflow_platform.auth import Role, current_user, require_roles
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    WorkflowInstance,
)
from workflow_platform.workflow import WorkflowDefinition


def build_router(repositories: Repositories) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/workflows", response_model=list[WorkflowDefinition])
    async def list_workflows(_: UserIdentity = Depends(current_user)) -> list[WorkflowDefinition]:
        return await repositories.definitions.list_all()

    @router.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
    async def get_workflow(
        workflow_id: str, _: UserIdentity = Depends(current_user)
    ) -> WorkflowDefinition:
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        return definition

    @router.get("/workflow-instances/{instance_id}")
    async def get_instance(
        instance_id: str, _: UserIdentity = Depends(current_user)
    ) -> dict[str, Any]:
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        steps = await repositories.steps.list_by_instance(instance_id)
        return {"instance": instance.model_dump(), "steps": [s.model_dump() for s in steps]}

    @router.get(
        "/workflow-instances/{instance_id}/audit",
        response_model=list[AuditEntry],
    )
    async def list_instance_audit(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
    ) -> list[AuditEntry]:
        return await repositories.audit.list_by_instance(instance_id)

    @router.get("/audit", response_model=list[AuditEntry])
    async def list_recent_audit(
        limit: int = 100,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
    ) -> list[AuditEntry]:
        return await repositories.audit.list_recent(limit=min(limit, 500))

    return router


__all__ = ["AuditEntry", "StepExecution", "WorkflowInstance", "build_router"]
