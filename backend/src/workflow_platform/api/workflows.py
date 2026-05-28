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
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from workflow_platform.auth import Role, current_user, require_roles
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.cost import CostReportService
from workflow_platform.engine import WorkflowEngine
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.triggers import WebhookRegistry
from workflow_platform.workflow import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    dump_definition_to_json,
    dump_definition_to_yaml,
    load_definition,
    load_definition_from_yaml,
)


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

    @router.get("/workflows/instance-counts")
    async def workflows_instance_counts(
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, int]:
        """Map of `workflow_id → instance count` across all instances ever
        recorded. Used by the workflows list page to show a count per row.
        Separate from `/api/workflows` so the (heavier) count query only
        runs when the count is actually wanted."""
        return await repositories.instances.count_by_workflow()

    @router.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
    async def get_workflow(
        workflow_id: str, _: UserIdentity = Depends(current_user)
    ) -> WorkflowDefinition:
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        return definition

    @router.get("/workflows/{workflow_id}/export")
    async def export_workflow(
        workflow_id: str,
        format: str = "json",
        _: UserIdentity = Depends(current_user),
    ) -> Response:
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        fmt = format.lower()
        if fmt == "json":
            return Response(
                content=dump_definition_to_json(definition),
                media_type="application/json",
            )
        if fmt in ("yaml", "yml"):
            return Response(
                content=dump_definition_to_yaml(definition),
                media_type="application/yaml",
            )
        raise HTTPException(status_code=400, detail=f"Unknown format: {format!r}")

    @router.post("/workflows/import")
    async def import_workflow(
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.DESIGNER)),
    ) -> dict[str, Any]:
        body = await request.body()
        text = body.decode("utf-8") if body else ""
        if not text.strip():
            raise HTTPException(status_code=400, detail="Empty body")
        content_type = (request.headers.get("content-type") or "").lower()
        is_yaml = "yaml" in content_type or not text.lstrip().startswith(("{", "["))
        try:
            if is_yaml:
                definition = load_definition_from_yaml(text)
            else:
                definition = load_definition(json.loads(text))
        except (WorkflowDefinitionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await repositories.definitions.save(definition)
        return {"status": "imported", "workflow_id": definition.id}

    @router.post("/workflows/{workflow_id}/run")
    async def run_workflow(
        workflow_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        """Manually fire a workflow once with a caller-supplied trigger payload.

        Body: JSON object accepted verbatim as the trigger payload. Empty body
        is treated as `{}`. Returns the new instance's id + state synchronously
        — the engine.run call is awaited so callers can navigate straight to
        the dashboard."""
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Run requires a WorkflowEngine bound to the API."
            )
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")
        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Trigger payload must be a JSON object")

        instance = await engine.run(definition, trigger_payload=payload)
        return {
            "status": "started",
            "instance_id": instance.id,
            "state": instance.state.value,
        }

    @router.get("/workflow-instances")
    async def list_instances(
        workflow_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        _: UserIdentity = Depends(current_user),
    ) -> list[dict[str, Any]]:
        if workflow_id:
            items = await repositories.instances.list_by_workflow(workflow_id)
        else:
            # No global list method on the repo yet — list across known definitions.
            items = []
            for definition in await repositories.definitions.list_all():
                items.extend(await repositories.instances.list_by_workflow(definition.id))
        if state:
            items = [i for i in items if i.state.value == state]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [i.model_dump() for i in items[: max(1, min(limit, 200))]]

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

    @router.post("/workflow-instances/{instance_id}/retry")
    async def retry_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Retry requires a WorkflowEngine bound to the API."
            )
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        if instance.state != WorkflowInstanceState.FAILED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot retry: instance is {instance.state.value}",
            )
        definition = await repositories.definitions.get(instance.workflow_id)
        if definition is None:
            raise HTTPException(
                status_code=400,
                detail=f"Definition {instance.workflow_id} not found; cannot retry.",
            )
        # The engine's resume path re-runs failed steps (already_done filters
        # only COMPLETED + SKIPPED).
        instance.state = WorkflowInstanceState.PAUSED
        await repositories.instances.update(instance)
        task = asyncio.create_task(engine.resume(definition, instance_id))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return {"status": "retry_started", "instance_id": instance_id}

    @router.post("/workflow-instances/{instance_id}/fork")
    async def fork_instance(
        instance_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        """Fork a prior instance at a specific step.

        Body: `{"from_step_id": "<step-id>"}`. Creates a new instance with
        the original's topological ancestors of `from_step_id` already
        marked completed (their outputs preserved), and re-runs everything
        from `from_step_id` onward — picking up any agent-memory edits
        since the source run. The source instance is unchanged.
        """
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Fork requires a WorkflowEngine bound to the API."
            )
        source = await repositories.instances.get(instance_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        definition = await repositories.definitions.get(source.workflow_id)
        if definition is None:
            raise HTTPException(
                status_code=400,
                detail=f"Definition {source.workflow_id} not found; cannot fork.",
            )

        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        from_step_id = payload.get("from_step_id") if isinstance(payload, dict) else None
        if not isinstance(from_step_id, str) or not from_step_id:
            raise HTTPException(status_code=400, detail="Body must include `from_step_id` (string)")

        try:
            new_instance = await engine.fork(definition, instance_id, from_step_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "forked",
            "source_instance_id": instance_id,
            "instance_id": new_instance.id,
            "state": new_instance.state.value,
        }

    @router.post("/workflow-instances/{instance_id}/kill")
    async def kill_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        if instance.state in (
            WorkflowInstanceState.COMPLETED,
            WorkflowInstanceState.FAILED,
            WorkflowInstanceState.KILLED,
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot kill: instance is already terminal ({instance.state.value})",
            )
        instance.state = WorkflowInstanceState.KILLED
        await repositories.instances.update(instance)
        return {"status": "kill_requested", "instance_id": instance_id}

    @router.delete("/workflow-instances/{instance_id}", status_code=204)
    async def delete_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> Response:
        """Hard-delete a terminal instance + its step_executions.

        Audit entries referencing the instance are intentionally left in
        place: the audit log is append-only by design, so the history
        of what happened survives the cleanup of what currently exists.

        Refuses on non-terminal states (running / pending / paused) — kill
        first if the operator wants to stop a live run.
        """
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        terminal = {
            WorkflowInstanceState.COMPLETED,
            WorkflowInstanceState.FAILED,
            WorkflowInstanceState.KILLED,
        }
        if instance.state not in terminal:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete: instance is {instance.state.value}. "
                    f"Kill it first, then delete."
                ),
            )
        await repositories.steps.delete_by_instance(instance_id)
        await repositories.instances.delete(instance_id)
        return Response(status_code=204)

    @router.delete("/workflow-instances")
    async def delete_instances_bulk(
        state: list[str] = Query(default=...),
        workflow_id: str | None = Query(default=None),
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, int]:
        """Bulk hard-delete every instance whose state is in `state` (one
        or more `?state=` query params). Cascades to step_executions.

        Refuses if any `state` value is non-terminal — bulk delete is for
        cleanup of finished runs, not stopping live ones. Optional
        `workflow_id` scopes to a single workflow definition.

        Returns counts: `{deleted_instances, deleted_steps}`. Audit
        entries are preserved, same as the single-instance DELETE.
        """
        terminal = {
            WorkflowInstanceState.COMPLETED.value,
            WorkflowInstanceState.FAILED.value,
            WorkflowInstanceState.KILLED.value,
        }
        requested = set(state)
        non_terminal = requested - terminal
        if non_terminal:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Refusing bulk delete: states {sorted(non_terminal)!r} are not "
                    f"terminal. Allowed: {sorted(terminal)!r}."
                ),
            )
        if not requested:
            raise HTTPException(status_code=400, detail="At least one ?state= parameter required.")

        deleted_ids = await repositories.instances.delete_by_states(
            list(requested), workflow_id=workflow_id
        )
        deleted_steps = await repositories.steps.delete_by_instances(deleted_ids)
        return {
            "deleted_instances": len(deleted_ids),
            "deleted_steps": deleted_steps,
        }

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

    @router.get("/escalations")
    async def list_escalations(
        state: str = "pending",
        limit: int = 50,
        _: UserIdentity = Depends(current_user),
    ) -> list[dict[str, Any]]:
        # Walk the recent audit log; pair `escalation_requested` with
        # `escalation_resolved` entries that reference them.
        audit = await repositories.audit.list_recent(limit=2000)
        resolved_ids: set[str] = {
            str(e.detail.get("original_id"))
            for e in audit
            if e.action == "escalation_resolved" and e.detail.get("original_id")
        }
        requested = [e for e in audit if e.action == "escalation_requested"]
        if state == "pending":
            requested = [e for e in requested if e.id not in resolved_ids]
        return [
            {
                "id": e.id,
                "instance_id": e.workflow_instance_id,
                "step_id": e.step_id,
                "actor_id": e.actor_id,
                "reason": e.detail.get("reason"),
                "context": e.detail.get("context"),
                "created_at": e.timestamp.isoformat(),
                "resolved": e.id in resolved_ids,
            }
            for e in requested[: max(1, min(limit, 200))]
        ]

    @router.post("/escalations/{escalation_id}/resolve")
    async def resolve_escalation(
        escalation_id: str,
        body: dict[str, Any],
        user: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR)),
    ) -> dict[str, Any]:
        from workflow_platform.persistence.models import (
            AuditEntry as _AuditEntry,
        )
        from workflow_platform.persistence.models import (
            _new_id,
            _utcnow,
        )

        audit = await repositories.audit.list_recent(limit=2000)
        requested = next(
            (e for e in audit if e.id == escalation_id and e.action == "escalation_requested"),
            None,
        )
        if requested is None:
            raise HTTPException(status_code=404, detail="Escalation not found")

        already = any(
            e.action == "escalation_resolved" and e.detail.get("original_id") == escalation_id
            for e in audit
        )
        if already:
            raise HTTPException(status_code=400, detail="Escalation already resolved")

        await repositories.audit.append(
            _AuditEntry(
                id=_new_id(),
                timestamp=_utcnow(),
                actor_type="human",
                actor_id=user.sub,
                action="escalation_resolved",
                workflow_instance_id=requested.workflow_instance_id,
                step_id=requested.step_id,
                detail={
                    "original_id": escalation_id,
                    "resolution": body.get("resolution", ""),
                },
            )
        )
        return {"status": "resolved", "escalation_id": escalation_id}

    cost_service = CostReportService(repositories)

    def _parse_since(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid `since`: {exc}") from exc

    @router.get("/cost/by-workflow")
    async def cost_by_workflow(
        since: str | None = None,
        _: UserIdentity = Depends(current_user),
    ) -> list[dict[str, Any]]:
        rows = await cost_service.by_workflow(_parse_since(since))
        return [
            {
                "workflow_id": r.key,
                "total_cost_usd": r.total_cost_usd,
                "total_tokens": r.total_tokens,
                "step_count": r.step_count,
            }
            for r in rows
        ]

    @router.get("/cost/by-model")
    async def cost_by_model(
        since: str | None = None,
        _: UserIdentity = Depends(current_user),
    ) -> list[dict[str, Any]]:
        rows = await cost_service.by_model(_parse_since(since))
        return [
            {
                "model": r.key,
                "total_cost_usd": r.total_cost_usd,
                "total_tokens": r.total_tokens,
                "step_count": r.step_count,
            }
            for r in rows
        ]

    @router.get("/cost/by-day")
    async def cost_by_day(
        since: str | None = None,
        _: UserIdentity = Depends(current_user),
    ) -> list[dict[str, Any]]:
        rows = await cost_service.by_day(_parse_since(since))
        return [
            {
                "date": r.key,
                "total_cost_usd": r.total_cost_usd,
                "total_tokens": r.total_tokens,
                "step_count": r.step_count,
            }
            for r in rows
        ]

    return router


__all__ = ["AuditEntry", "StepExecution", "WorkflowInstance", "build_router"]
