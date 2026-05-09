"""HTTP routes for workflow definitions, instances, audit log, lifecycle
operations (pause/resume), and webhook triggers.

Role gating (per `docs/ARCHITECTURE.md` D4):
- Read endpoints: any authenticated role.
- Audit endpoints: Admin or Auditor.
- Lifecycle ops (pause/resume): Admin or Operator.
- Webhook fire: not authenticated by user (production must add HMAC or
  shared-secret verification; out of scope for Week 5).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from workflow_platform.auth import Role, current_user, require_roles
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.engine import WorkflowEngine
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.triggers import WebhookRegistry
from workflow_platform.workflow import WorkflowDefinition


def build_router(
    repositories: Repositories,
    *,
    engine: WorkflowEngine | None = None,
    webhook_registry: WebhookRegistry | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    # Hold strong refs to background tasks (resume) so the GC doesn't drop
    # them mid-flight. Tasks self-discard on completion.
    background_tasks: set[asyncio.Task[Any]] = set()

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

    @router.post("/workflow-instances/{instance_id}/pause")
    async def pause_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        if instance.state != WorkflowInstanceState.RUNNING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot pause: instance is {instance.state.value}",
            )
        instance.state = WorkflowInstanceState.PAUSED
        await repositories.instances.update(instance)
        return {"status": "pause_requested", "instance_id": instance_id}

    @router.post("/workflow-instances/{instance_id}/resume")
    async def resume_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Resume requires a WorkflowEngine bound to the API."
            )
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        if instance.state != WorkflowInstanceState.PAUSED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume: instance is {instance.state.value}",
            )
        definition = await repositories.definitions.get(instance.workflow_id)
        if definition is None:
            raise HTTPException(
                status_code=400,
                detail=f"Definition {instance.workflow_id} not found; cannot resume.",
            )
        # Resume in the background; clients poll the instance for completion.
        task = asyncio.create_task(engine.resume(definition, instance_id))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return {"status": "resume_started", "instance_id": instance_id}

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

    if webhook_registry is not None:

        @router.post("/triggers/webhook/{trigger_id}")
        async def fire_webhook(trigger_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            fired = await webhook_registry.fire(trigger_id, payload)
            if not fired:
                raise HTTPException(
                    status_code=404, detail=f"No webhook trigger registered for {trigger_id!r}"
                )
            return {"status": "fired", "trigger_id": trigger_id}

    return router


__all__ = ["AuditEntry", "StepExecution", "WorkflowInstance", "build_router"]
