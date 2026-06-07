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
import dataclasses
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from workflow_platform.auth import Role, current_user, require_roles
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.catalog import build_catalog
from workflow_platform.cost import CostReportService, price_for_model
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecution,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.scaffold import (
    DEFAULT_SCAFFOLD_MODEL,
    ScaffoldError,
    scaffold_workflow,
)
from workflow_platform.security import CapabilityPolicy, resolve_capabilities
from workflow_platform.templates import default_examples_dir, load_templates, slugify, unique_id
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.triggers import WebhookRegistry
from workflow_platform.workflow import (
    TriggerSpec,
    WorkflowDefinition,
    WorkflowDefinitionError,
    dump_definition_to_json,
    dump_definition_to_yaml,
    load_definition,
    load_definition_from_yaml,
    validate_and_order,
    validate_definition,
)
from workflow_platform.world import mock_world

_DRY_RUN_EXTERNAL_PREFIXES = ("email_", "connector_", "browser_")


def _is_external_tool(name: str) -> bool:
    """Tools that reach outside the sandbox (mailbox / network / browser). In a
    dry run these are replaced with no-op stubs so a test touches nothing real."""
    return name.startswith(_DRY_RUN_EXTERNAL_PREFIXES)


def _sandbox_tool(original: Tool) -> Tool:
    """A no-op stand-in for an external tool, keeping its name/description/schema
    so the agent still *sees* and can *call* it — the call just does nothing and
    returns a sandbox notice. (We can't simply drop the tool: the engine fails a
    step that references a tool missing from the catalog.)"""

    class _Sandboxed(Tool):
        name = original.name
        description = original.description
        parameters_schema = original.parameters_schema

        async def execute(
            self, params: dict[str, Any], context: ToolContext | None = None
        ) -> ToolResult:
            return ToolResult(
                content={"sandboxed": True, "note": f"{original.name} not executed (dry run)"}
            )

    return _Sandboxed()


def _excerpt(value: Any, limit: int = 800) -> str | None:
    """Bound a value for the explain view: strings pass through, other values
    are JSON-encoded; anything over `limit` is truncated with a marker."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    return s if len(s) <= limit else s[:limit] + f"… (+{len(s) - limit} more chars)"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _denying_capability_layer(
    tool: str, named_layers: list[tuple[str, CapabilityPolicy | None]]
) -> str:
    """Which named layer's tools-allowlist excludes `tool` (first match). For
    display only — the allow/deny decision itself goes through
    ResolvedCapabilities.tool_allowed, not this."""
    for label, layer in named_layers:
        if layer is not None and layer.tools is not None and tool not in layer.tools:
            return label
    return "capability"


def build_router(
    repositories: Repositories,
    *,
    engine: WorkflowEngine | None = None,
    webhook_registry: WebhookRegistry | None = None,
    templates_dir: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    # Source for the templates gallery (canvas roadmap C5.2). Defaults to the
    # same repo-root `examples` dir the trigger orchestrator loads from
    # (resolved CWD-independently — see default_examples_dir).
    _templates_dir = templates_dir or default_examples_dir()
    # Hold strong refs to background tasks (resume) so the GC doesn't drop
    # them mid-flight. Tasks self-discard on completion.
    background_tasks: set[asyncio.Task[Any]] = set()

    @router.get("/workflows", response_model=list[WorkflowDefinition])
    async def list_workflows(_: UserIdentity = Depends(current_user)) -> list[WorkflowDefinition]:
        return await repositories.definitions.list_all()

    @router.get("/templates")
    async def list_templates(_: UserIdentity = Depends(current_user)) -> list[dict[str, Any]]:
        """Bundled example workflows offered as starting points in the GUI.

        Returns lightweight summaries (the gallery shows cards); cloning a
        template into a new editable workflow goes through `POST /api/workflows`
        with `{"template_id": ...}`."""
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "step_count": len(t.steps),
                "trigger_type": t.trigger.type,
            }
            for t in load_templates(_templates_dir)
        ]

    @router.post("/workflows", response_model=WorkflowDefinition, status_code=201)
    async def create_workflow(
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.DESIGNER)),
    ) -> WorkflowDefinition:
        """Create a new workflow definition — blank, or cloned from a template.

        Body (JSON object, all optional):
        - `name`: the new workflow's name (id is slugified from it).
        - `template_id`: clone this bundled template instead of starting blank.

        Empty body creates a blank manual-trigger workflow with no steps. The
        new id is slugified from the name and de-duplicated against existing
        ids. Returns the persisted definition so the caller can open it on the
        canvas (typically in edit mode)."""
        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            spec = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(spec, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")

        name = spec.get("name")
        template_id = spec.get("template_id")
        if name is not None and not isinstance(name, str):
            raise HTTPException(status_code=400, detail="`name` must be a string")
        if template_id is not None and not isinstance(template_id, str):
            raise HTTPException(status_code=400, detail="`template_id` must be a string")

        existing = {d.id for d in await repositories.definitions.list_all()}

        if template_id:
            sources = {t.id: t for t in load_templates(_templates_dir)}
            source = sources.get(template_id)
            if source is None:
                raise HTTPException(status_code=404, detail=f"Template {template_id!r} not found")
            new_name = name or f"{source.name} (copy)"
            new_id = unique_id(slugify(new_name), existing)
            definition = source.model_copy(deep=True, update={"id": new_id, "name": new_name})
        else:
            new_name = name or "Untitled workflow"
            new_id = unique_id(slugify(new_name), existing)
            definition = WorkflowDefinition(
                id=new_id,
                name=new_name,
                description="",
                trigger=TriggerSpec(type="manual", example_payload={}),
                steps=[],
                edges=[],
            )

        # Cheap structural check before persisting (empty graphs are valid).
        validate_and_order(definition)
        await repositories.definitions.save(definition)
        return definition

    @router.post("/workflows/scaffold")
    async def scaffold_workflow_endpoint(
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.DESIGNER)),
    ) -> dict[str, Any]:
        """NL scaffold (C7.1): turn a plain-English description into a draft
        workflow and persist it as an editable starting point.

        Body: `{"description": "<what the workflow should do>"}`. One Bedrock call,
        handed the live catalog so it only references real triggers / functions /
        tools. The result is id'd, structurally validated, and saved; the caller
        opens it on the canvas (edit mode) to refine. Returns the new id + any
        advisory findings (e.g. disconnected steps)."""
        if engine is None:
            raise HTTPException(status_code=503, detail="No workflow engine is configured.")

        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            spec = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        description = spec.get("description") if isinstance(spec, dict) else None
        if not isinstance(description, str) or not description.strip():
            raise HTTPException(status_code=400, detail="`description` is required.")

        catalog = build_catalog(engine.functions, engine.tools)
        model = os.environ.get("WORKFLOW_PLATFORM_SCAFFOLD_MODEL", DEFAULT_SCAFFOLD_MODEL)
        try:
            raw = await scaffold_workflow(
                engine.bedrock, model=model, description=description, catalog=catalog
            )
        except ScaffoldError as exc:
            raise HTTPException(
                status_code=422, detail=f"Couldn't scaffold a workflow: {exc}"
            ) from exc

        existing = {d.id for d in await repositories.definitions.list_all()}
        name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else None
        new_name = name or "Scaffolded workflow"
        raw["name"] = new_name
        raw["id"] = unique_id(slugify(new_name), existing)
        raw.setdefault("description", "")
        raw.setdefault("trigger", {"type": "manual", "config": {}})

        try:
            definition = WorkflowDefinition.model_validate(raw)
        except ValidationError as exc:
            first = "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
                for e in exc.errors()[:3]
            )
            raise HTTPException(
                status_code=422, detail=f"Scaffolded workflow was malformed: {first}"
            ) from exc

        # Persisted definitions are always structurally runnable-shaped (matches
        # create/import). Advisory findings (warnings) are returned, not blocking.
        try:
            validate_and_order(definition)
        except WorkflowDefinitionError as exc:
            raise HTTPException(
                status_code=422, detail=f"Scaffolded workflow was structurally invalid: {exc}"
            ) from exc

        await repositories.definitions.save(definition)
        findings = [f.model_dump() for f in validate_definition(definition)]
        return {
            "status": "created",
            "workflow_id": definition.id,
            "name": definition.name,
            "findings": findings,
        }

    @router.get("/catalog")
    async def get_catalog(
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, Any]:
        """Authoring catalog (C7.2): the trigger types, deterministic functions,
        and agent tools the canvas offers as a searchable palette. Reflects the
        engine's live registries so it never offers an unwired building block."""
        functions = engine.functions if engine is not None else default_function_registry()
        tools = engine.tools if engine is not None else ToolCatalog([])
        return build_catalog(functions, tools).model_dump()

    @router.post("/workflows/validate")
    async def validate_workflow(
        request: Request,
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, Any]:
        """Validate a (possibly unsaved) workflow definition for the canvas (C7.3).

        Body is a full WorkflowDefinition JSON. Returns every structural finding
        at once — keyed to the node/edge it concerns — so the canvas can light up
        red borders + inline messages before a save or run."""
        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            spec = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(spec, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")

        try:
            definition = WorkflowDefinition.model_validate(spec)
        except ValidationError as exc:
            steps_raw = spec.get("steps")
            raw_steps: list[Any] = steps_raw if isinstance(steps_raw, list) else []
            findings = []
            for err in exc.errors():
                loc = err.get("loc", ())
                node_id = None
                if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
                    step = raw_steps[loc[1]] if loc[1] < len(raw_steps) else {}
                    node_id = step.get("id") if isinstance(step, dict) else None
                findings.append(
                    {
                        "level": "error",
                        "code": "parse_error",
                        "message": f"{'.'.join(str(p) for p in loc)}: {err.get('msg', '')}".lstrip(
                            ": "
                        ),
                        "node_id": node_id,
                        "edge": None,
                    }
                )
            return {"valid": False, "findings": findings}

        findings_models = validate_definition(definition)
        return {
            "valid": not any(f.level == "error" for f in findings_models),
            "findings": [f.model_dump() for f in findings_models],
        }

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

    @router.delete("/workflows/{workflow_id}")
    async def delete_workflow(
        workflow_id: str,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.DESIGNER)),
    ) -> dict[str, Any]:
        """Hard-delete a workflow definition and cascade to its run history
        (instances + their step_executions). Admin/Designer only.

        Returns counts: `{deleted_workflow, deleted_instances, deleted_steps}`.
        Audit entries are immutable and preserved (same as instance deletes).

        Note: this does not unregister an already-running in-process trigger for
        the workflow (filesystem / schedule / webhook / gmail_poll) — restart the
        server to fully clear it. Bundled examples are re-seeded on restart by the
        trigger orchestrator, so deleting one only clears it until the next boot."""
        if await repositories.definitions.get(workflow_id) is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

        instances = await repositories.instances.list_by_workflow(workflow_id)
        instance_ids = [i.id for i in instances]
        deleted_steps = (
            await repositories.steps.delete_by_instances(instance_ids) if instance_ids else 0
        )
        deleted_instances = 0
        for instance_id in instance_ids:
            if await repositories.instances.delete(instance_id):
                deleted_instances += 1

        await repositories.definitions.delete(workflow_id)
        return {
            "deleted_workflow": workflow_id,
            "deleted_instances": deleted_instances,
            "deleted_steps": deleted_steps,
        }

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

    @router.post("/workflows/{workflow_id}/dry-run")
    async def dry_run_workflow(
        workflow_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.DESIGNER)),
    ) -> dict[str, Any]:
        """Run a workflow once in a sandbox (C6.1): a `MockWorld` (no real file /
        database / messaging side effects) and a tool catalog with the external
        tools (email / connector / browser) removed, but **live Bedrock** so the
        agent reasons for real — "sandbox the world, keep the brain". The
        instance is persisted and tagged `dry_run` so the canvas can follow it.

        Browser-automation workflows are rejected (a real browser can't be
        sandboxed yet)."""
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Dry-run requires a WorkflowEngine bound to the API."
            )
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")
        uses_browser = any(
            s.type == "agentic" and any(t.startswith("browser_") for t in s.tools)
            for s in definition.steps
        )
        if uses_browser:
            raise HTTPException(
                status_code=400,
                detail="Dry-run isn't supported for browser-automation workflows yet.",
            )
        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Trigger payload must be a JSON object")

        sandbox_tools: list[Tool] = []
        for tool_name in engine.tools.names():
            tool = engine.tools.get(tool_name)
            if tool is None:
                continue
            sandbox_tools.append(_sandbox_tool(tool) if _is_external_tool(tool_name) else tool)
        dry_engine = dataclasses.replace(
            engine, world=mock_world(), tools=ToolCatalog(sandbox_tools)
        )

        instance = await dry_engine.run(definition, trigger_payload=payload)
        # Tag in history so a dry run is distinguishable from a real one.
        instance.context = {**(instance.context or {}), "dry_run": True}
        await repositories.instances.update(instance)
        return {
            "status": "completed",
            "instance_id": instance.id,
            "state": instance.state.value,
            "dry_run": True,
            "error": instance.error,
            "sandbox": "MockWorld; external tools (email/connector/browser) disabled; live Bedrock",
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

    @router.get("/workflows/{workflow_id}/cost-estimate")
    async def workflow_cost_estimate(
        workflow_id: str,
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, Any]:
        """Pre-run cost context for the Run dialog (C6.2): per-agentic-step model
        rates, the budget policy, and the average cost/tokens per run from
        history (null when the workflow hasn't run yet)."""
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")
        models: list[dict[str, Any]] = []
        for step in definition.steps:
            if step.type == "agentic":
                price = price_for_model(step.model)
                models.append(
                    {
                        "step_id": step.id,
                        "model": step.model,
                        "input_per_million": price.input_per_million if price else None,
                        "output_per_million": price.output_per_million if price else None,
                    }
                )
        stats = await cost_service.run_stats_for_workflow(workflow_id)
        return {
            "workflow_id": workflow_id,
            "models": models,
            "run_count": stats.run_count,
            "avg_cost_usd": stats.avg_cost_usd,
            "avg_tokens": stats.avg_tokens,
            "max_total_tokens": definition.policies.max_total_tokens,
            "budget_action": definition.policies.budget_action,
        }

    @router.get("/workflows/{workflow_id}/capabilities")
    async def workflow_capabilities(
        workflow_id: str,
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, Any]:
        """Per-agentic-step tool capability boundary (C6.3): which catalog tools
        each step can use vs is denied, and why. Uses the same layer
        intersection the engine enforces (system -> workflow -> step)."""
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")
        catalog = engine.tools.names() if engine is not None else []
        catalog_set = set(catalog)
        system_caps = engine.system_capabilities if engine is not None else None

        steps_out: list[dict[str, Any]] = []
        for step in definition.steps:
            if step.type != "agentic":
                continue
            named_layers: list[tuple[str, CapabilityPolicy | None]] = [
                ("system", system_caps),
                ("workflow", definition.capabilities),
                ("step", step.capabilities),
            ]
            resolved = resolve_capabilities(system_caps, definition.capabilities, step.capabilities)
            offered = set(step.tools)
            allowed: list[str] = []
            denied: list[dict[str, str]] = []
            for tool in sorted(catalog_set | offered):
                in_catalog = tool in catalog_set
                in_offer = tool in offered
                if in_offer and in_catalog and resolved.tool_allowed(tool):
                    allowed.append(tool)
                elif in_offer and not in_catalog:
                    denied.append(
                        {
                            "tool": tool,
                            "reason": "Tool not available in this deployment",
                            "reason_code": "unknown_tool",
                        }
                    )
                elif not in_offer:
                    denied.append(
                        {
                            "tool": tool,
                            "reason": "Not enabled for this step",
                            "reason_code": "not_enabled",
                        }
                    )
                else:
                    label = _denying_capability_layer(tool, named_layers)
                    denied.append(
                        {
                            "tool": tool,
                            "reason": f"Blocked by the {label} capability allowlist",
                            "reason_code": "capability_blocked",
                        }
                    )
            steps_out.append(
                {
                    "step_id": step.id,
                    "model": step.model,
                    "allowed": allowed,
                    "denied": denied,
                }
            )
        return {"workflow_id": workflow_id, "tool_catalog": catalog, "steps": steps_out}

    @router.get("/workflow-instances/{instance_id}/steps/{step_id}/explain")
    async def explain_step(
        instance_id: str,
        step_id: str,
        _: UserIdentity = Depends(current_user),
    ) -> dict[str, Any]:
        """Forensic view of one step in a run (C6.4): for an agent step, what it
        was asked, the tools it called (args + results), tokens/cost, and the
        memory hash in effect; for a deterministic step, its function + output.
        Assembled from the step execution + the step's audit slice."""
        instance = await repositories.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Instance {instance_id!r} not found")
        execs = [
            e
            for e in await repositories.steps.list_by_instance(instance_id)
            if e.step_id == step_id
        ]
        if not execs:
            raise HTTPException(
                status_code=404, detail=f"Step {step_id!r} not found in instance {instance_id!r}"
            )
        exe = execs[-1]  # latest attempt (retries append)
        output = exe.output or {}

        # Static context from the definition (best-effort — may be gone).
        definition = await repositories.definitions.get(instance.workflow_id)
        step_def = (
            next((s for s in definition.steps if s.id == step_id), None)
            if definition is not None
            else None
        )
        kind = (
            step_def.type
            if step_def is not None
            else ("agentic" if "usage" in output else "deterministic")
        )

        audit = [
            a
            for a in await repositories.audit.list_by_instance(instance_id)
            if a.step_id == step_id
        ]
        tool_calls = [
            {
                "name": a.detail.get("name"),
                "input": _excerpt(a.detail.get("input")),
                "result": _excerpt(a.detail.get("result")),
                "timestamp": _iso(a.timestamp),
            }
            for a in audit
            if a.action == "tool_call"
        ]

        common: dict[str, Any] = {
            "instance_id": instance_id,
            "step_id": step_id,
            "state": exe.state.value,
            "kind": kind,
            "started_at": _iso(exe.started_at),
            "completed_at": _iso(exe.completed_at),
            "error": exe.error,
        }
        if kind == "agentic":
            usage = output.get("usage") or {}
            return {
                **common,
                "model": output.get("model"),
                "memory_hash": output.get("memory_hash"),
                "stop_reason": output.get("stop_reason"),
                "iterations": usage.get("iterations"),
                "usage": usage,
                "cost_usd": output.get("cost_usd"),
                "goal": _excerpt(getattr(step_def, "goal", None)),
                "system_prompt": _excerpt(getattr(step_def, "system_prompt", None)),
                "output_text": _excerpt(output.get("output_text")),
                "tool_calls": tool_calls,
            }
        return {
            **common,
            "function": getattr(step_def, "function", None),
            "config": _excerpt(getattr(step_def, "config", None)),
            "output": _excerpt(output),
        }

    return router


__all__ = ["AuditEntry", "StepExecution", "WorkflowInstance", "build_router"]
