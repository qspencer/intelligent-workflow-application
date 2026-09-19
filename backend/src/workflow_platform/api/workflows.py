"""HTTP routes for workflow definitions, instances, audit log, lifecycle
operations (pause/resume), and webhook triggers.

Role gating (per `docs/ARCHITECTURE.md` D4):
- Read endpoints: any authenticated role.
- Audit endpoints: Admin or Auditor.
- Lifecycle ops (pause/resume): Admin or Operator.
- Webhook fire: not authenticated by user; HMAC-verified instead when the
  trigger's config names a `secret_name` (G2). Unsigned only for local dev.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from workflow_platform.api.raw_trace_audit import (
    SURFACE_AUDIT,
    SURFACE_DETAIL,
    SURFACE_ESCALATION,
    SURFACE_EXPLAIN,
    begin_raw_release,
    commit_raw_release,
)
from workflow_platform.api.redaction import (
    has_redaction_marker,
    project_audit_detail,
    redact_error,
    redact_tool_data,
)
from workflow_platform.auth import auth_mode, current_user, require_roles
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.auth.rbac import ANY_ROLE, ORG_ADMIN_ROLES, ORG_WRITE_ROLES, Role
from workflow_platform.auth.scope import OrgScope, resolve_org_scope
from workflow_platform.catalog import build_catalog
from workflow_platform.cost import CostReportService, price_for_model
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.memory import LearnedMemoryService
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
    mint_platform_step_ids,
    scaffold_workflow,
)
from workflow_platform.secrets import SecretNotFoundError, SecretStore
from workflow_platform.security import CapabilityPolicy, resolve_capabilities
from workflow_platform.templates import default_examples_dir, load_templates, slugify, unique_id
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.trace_rehydrate import RawTraceRehydrator, RawTraceUnavailable
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

logger = logging.getLogger(__name__)

_DRY_RUN_EXTERNAL_PREFIXES = ("email_", "connector_", "browser_")

# Batch run (C8.1): cap total rows so one request can't run unbounded, and cap
# in-flight runs so a batch doesn't stampede Bedrock.
_BATCH_MAX = 100
_BATCH_CONCURRENCY = 5


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


# Shown in place of a raw field on a below-grant read (P2 — one marker across
# every raw-capable surface: explain, escalations, dry-run, run-batch).
_REDACTED_GRANT_ONLY = "[redacted — raw-trace grant required]"


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
    secret_store: SecretStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    # Source for the templates gallery (canvas roadmap C5.2). Defaults to the
    # same repo-root `examples` dir the trigger orchestrator loads from
    # (resolved CWD-independently — see default_examples_dir).
    _templates_dir = templates_dir or default_examples_dir()
    # Hold strong refs to background tasks (resume) so the GC doesn't drop
    # them mid-flight. Tasks self-discard on completion.
    background_tasks: set[asyncio.Task[Any]] = set()

    async def _audit_admin_action(
        user: UserIdentity,
        action: str,
        *,
        instance_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Destructive admin operations must leave a trace — the audit log is
        the platform's memory of what happened, and that includes deletions.
        (Added after a bulk instance wipe was only reconstructable from HTTP
        access logs.)"""
        await repositories.audit.append(
            AuditEntry(
                actor_type="human",
                actor_id=user.sub,
                action=action,
                workflow_instance_id=instance_id,
                detail=detail or {},
            )
        )

    async def _org_scope(user: UserIdentity = Depends(current_user)) -> OrgScope:
        return await resolve_org_scope(repositories, user)

    async def _visible_definition(workflow_id: str, scope: OrgScope) -> None:
        """404 when the definition belongs to another org (ROLES_PLAN §7.1:
        cross-org resources are invisible, not forbidden)."""
        if scope.org_id is None:
            return
        org = await repositories.definitions.org_of(workflow_id)
        if org is not None and org != scope.org_id:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    async def _visible_instance(instance_id: str, scope: OrgScope) -> WorkflowInstance:
        instance = await repositories.instances.get(instance_id)
        if instance is None or (scope.org_id is not None and instance.org_id != scope.org_id):
            raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
        return instance

    def _reject_dry_run(instance: WorkflowInstance, verb: str) -> None:
        """A dry run is a probe. Re-driving one would execute it against the
        real world — the sandbox lives in the engine that ran it, not in the
        record. The engine refuses too (`_refuse_if_dry_run`); this is here
        so the operator gets a 400 with a reason instead of a 500."""
        if (instance.context or {}).get("dry_run"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot {verb}: this instance was a dry run, and {verb} would "
                    "execute it for real. Run the workflow instead."
                ),
            )

    def _bypass(scope: OrgScope, resource_org: str) -> dict[str, Any]:
        """ROLES_PLAN §2.4: an Administrator acting outside their own org is
        an explicit, audited bypass — never a missing filter."""
        if scope.is_administrator and resource_org != scope.home_org_id:
            return {"org_bypass": True}
        return {}

    async def _note_bypass(
        scope: OrgScope, resource_org: str, action: str, instance_id: str | None = None
    ) -> None:
        """Audit trace for cross-org mutations that otherwise write no
        API-side audit entry (lifecycle ops — the engine audits the effect,
        this records who reached across orgs to cause it)."""
        detail = _bypass(scope, resource_org)
        if detail:
            await repositories.audit.append(
                AuditEntry(
                    actor_type="human",
                    actor_id=scope.sub,
                    action=action,
                    workflow_instance_id=instance_id,
                    detail=detail,
                )
            )

    async def _attribution(
        user: UserIdentity, scope: OrgScope, explicit_org: str | None
    ) -> dict[str, str]:
        """Definition-create attribution. Administrators may target an
        explicit org (§2.6); everyone else creates in their own."""
        kwargs = await _owner_kwargs(user)
        if explicit_org is not None:
            if not scope.is_administrator:
                if explicit_org != scope.org_id:
                    raise HTTPException(
                        status_code=403, detail="Cannot create in another organization"
                    )
            elif await repositories.organizations.get(explicit_org) is None:
                raise HTTPException(status_code=400, detail=f"No such organization: {explicit_org}")
            kwargs["org_id"] = explicit_org
        return kwargs

    @router.get("/workflows", response_model=list[WorkflowDefinition])
    async def list_workflows(
        scope: OrgScope = Depends(_org_scope),
    ) -> list[WorkflowDefinition]:
        return await repositories.definitions.list_all(org_id=scope.org_id)

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

    @router.get("/me")
    async def whoami(
        request: Request, user: UserIdentity = Depends(current_user)
    ) -> dict[str, Any]:
        """The authenticated caller: IdP identity + the persisted platform
        user (JIT-provisioned) + org. The stable `user.id` is what features
        (ownership, per-user memory) should reference — never the raw sub."""
        persisted = await repositories.users.get_by_identity(current_issuer(), user.sub)
        org = await repositories.organizations.get(persisted.org_id) if persisted else None
        persisted_out = (
            persisted.model_dump(mode="json", exclude={"password_hash"}) if persisted else None
        )
        return {
            "auth_mode": auth_mode(),
            "identity": {"sub": user.sub, "email": user.email, "roles": user.roles},
            "user": persisted_out,
            "organization": org.model_dump(mode="json") if org else None,
        }

    async def _owner_kwargs(user: UserIdentity) -> dict[str, str]:
        """Ownership attribution for definition-creating endpoints."""
        persisted = await repositories.users.get_by_identity(current_issuer(), user.sub)
        if persisted is None:
            return {}
        return {"org_id": persisted.org_id, "owner_user_id": persisted.id}

    @router.post("/workflows", response_model=WorkflowDefinition, status_code=201)
    async def create_workflow(
        request: Request,
        org_id: str | None = Query(default=None),
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
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
        await repositories.definitions.save(definition, **await _attribution(user, scope, org_id))
        return definition

    @router.post("/workflows/scaffold")
    async def scaffold_workflow_endpoint(
        request: Request,
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        org_id: str | None = Query(default=None),
        scope: OrgScope = Depends(_org_scope),
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
        # R7 §4.4 (CONFIG ownership): the id was `slugify(<the model's proposed
        # name>)`, and the projection publishes `workflow_id` to ordinary
        # readers as CONFIG — operator-approved content. It was not: slugging
        # preserves whatever the model wrote, so a model-authored string sat in
        # a trusted position. The id is MINTED now, exactly as step ids are.
        # The model's `name` survives on the definition for display; no trace
        # kind declares a workflow name, so none of it reaches a trace.
        raw["id"] = unique_id(f"wf-{uuid4().hex[:8]}", existing)
        # R5 F4: the model names its own steps, and step ids are published as
        # dictionary KEYS in the projected `context.steps`. Mint platform ids
        # (rewriting every reference) so the projector's platform-keyed
        # declaration is true of what we persist.
        raw = mint_platform_step_ids(raw)
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

        await repositories.definitions.save(definition, **await _attribution(user, scope, org_id))
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
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, int]:
        """Map of `workflow_id → instance count` across all instances ever
        recorded. Used by the workflows list page to show a count per row.
        Separate from `/api/workflows` so the (heavier) count query only
        runs when the count is actually wanted."""
        return await repositories.instances.count_by_workflow(org_id=scope.org_id)

    _MEMORY_ADMIN_ROLES = (Role.ADMINISTRATOR, Role.ORG_ADMIN)
    # Raw tool payloads are read only with a covering raw-trace GRANT
    # (docs/TRACE_GOVERNANCE_PLAN.md §2, TG1) — a per-user privilege distinct
    # from administration, NOT a role. See `_raw_reader_for_org`.
    grant_service = RawTraceGrantService(repositories)
    # Read-surface raw-merge (TG3b.3): under the safe-only flip the operational
    # store is projected, so a grant-holder's raw is restored from the vault
    # here. A no-op under the default dark dual-write (nothing is projected).
    rehydrator = RawTraceRehydrator(repositories)
    _CATEGORIES_BYTE_CAP = 262_144

    @router.get("/memory/summary")
    async def memory_summary(
        user: UserIdentity = Depends(require_roles(*_MEMORY_ADMIN_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Memory transparency surface (VERACIUM_041_ADOPTION_PLAN §2b):
        namespaces visible to the caller with counts. Memory is tenant data —
        Org Admins see their own org only; the unrecognized-ids count (legacy
        store keys that render no rows) is Administrator-only."""
        if engine is None or engine.learned_memory is None:
            return {"namespaces": [], "unrecognized_ids": 0}
        data = await engine.learned_memory.list_memory_namespaces()
        if scope.org_id is not None:
            data["namespaces"] = [n for n in data["namespaces"] if n["org_id"] == scope.org_id]
            data.pop("unrecognized_ids", None)
        return data

    @router.get("/memory/summary/{org_id}/{account}")
    async def memory_introspect(
        org_id: str,
        account: str,
        mode: str = "summary",
        user: UserIdentity = Depends(require_roles(*_MEMORY_ADMIN_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Introspect one namespace, keyed by the full (org, account)
        identity. Cross-org for non-Administrators → 404, never 403 (S2's
        no-existence-leak rule); garbage params → 404, never 500. Audited
        (`memory_introspected`) — this view renders personal-data-heavy,
        attacker-authored mail content; access deserves a log line."""
        if mode not in ("summary", "categories"):
            raise HTTPException(status_code=400, detail="mode must be summary|categories")
        if scope.org_id is not None and org_id != scope.org_id:
            raise HTTPException(status_code=404, detail="No such memory namespace")
        if engine is None or engine.learned_memory is None:
            raise HTTPException(status_code=404, detail="No such memory namespace")
        namespace = f"org:{org_id}:user:{account}"
        listing = await engine.learned_memory.list_memory_namespaces()
        known = {(n["org_id"], n["account"]) for n in listing["namespaces"]}
        if (org_id, account) not in known:
            raise HTTPException(status_code=404, detail="No such memory namespace")

        # F3 (external code review 2026-08-02): `categories` renders fact
        # CONTENT verbatim — attacker-authored mail — so it is a raw release,
        # requiring a covering raw-trace GRANT (not merely a role) with the
        # attempt/release audit protocol. A cross-org read needs a target-org
        # or platform-wide grant (`_raw_reader_for_org` keys on org_id).
        # `summary` (counts only) stays role-gated.
        release_request_id: str | None = None
        if mode == "categories":
            if not await _raw_reader_for_org(user, org_id):
                raise HTTPException(
                    status_code=403, detail="categories mode requires a covering raw-trace grant"
                )
            release_request_id, _rr = await begin_raw_release(
                repositories,
                raw_ok=True,
                surface="memory",
                actor_id=user.sub,
                instance_id=None,
                kinds=["memory_facts"],
            )
            if release_request_id is None:
                mode = "summary"  # attempt audit unavailable → fail closed to counts

        result = await engine.learned_memory.introspect_namespace(namespace, mode=mode)
        truncated = False
        if mode == "categories" and len(json.dumps(result)) > _CATEGORIES_BYTE_CAP:
            # Byte cap (review finding 4): fall back to the always-intact
            # summary counts + an explicit flag; pagination waits for a real
            # operator hitting this.
            result = await engine.learned_memory.introspect_namespace(namespace, mode="summary")
            truncated = True
        if release_request_id is not None:
            facts_released = mode == "categories" and not truncated
            audit_ok, _rr = await commit_raw_release(
                repositories,
                request_id=release_request_id,
                surface="memory",
                actor_id=user.sub,
                instance_id=None,
                returned_kinds=["memory_facts"] if facts_released else (),
                withheld_kinds=() if facts_released else ["memory_facts"],
            )
            # F3 (re-review): the release-decision audit is a PRECONDITION for
            # facts crossing the boundary. If it failed to commit, discard the
            # facts and fall back to counts — no raw byte leaves un-audited.
            if facts_released and not audit_ok:
                result = await engine.learned_memory.introspect_namespace(namespace, mode="summary")
                mode = "summary"
                truncated = False
        await repositories.audit.append(
            AuditEntry(
                actor_type="human",
                actor_id=user.sub,
                action="memory_introspected",
                detail={
                    "namespace": namespace,
                    "mode": mode,
                    **_bypass(scope, org_id),
                },
            )
        )
        return {**result, "namespace": namespace, "mode": mode, "truncated": truncated}

    @router.get("/workflows/attribution")
    async def workflows_attribution(
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, dict[str, Any]]:
        """The IA_PLAN attribution/metadata sidecar: per-definition row facts
        that deliberately do NOT live on the YAML-shaped model — org + owner
        (display name resolved server-side; non-admins can't call /api/users),
        bundled-ness as authoritative metadata (same templates dir the
        orchestrator seeds), and the run-effect classification (worst over the
        definition's tools; unknown counts as mutating — IA_PLAN §4e)."""
        ownership = await repositories.definitions.list_ownership(org_id=scope.org_id)

        template_ids = {tpl.id for tpl in load_templates(default_examples_dir())}
        org_names: dict[str, str] = {}
        owner_names: dict[str, str | None] = {}
        out: dict[str, dict[str, Any]] = {}
        for definition_id, (org_id, owner_user_id) in ownership.items():
            if org_id not in org_names:
                org = await repositories.organizations.get(org_id)
                org_names[org_id] = org.name if org else org_id
            entry: dict[str, Any] = {"org_id": org_id, "org_name": org_names[org_id]}
            if owner_user_id:
                if owner_user_id not in owner_names:
                    owner = await repositories.users.get(owner_user_id)
                    owner_names[owner_user_id] = (
                        (owner.display_name or owner.email) if owner else None
                    )
                entry["owner_user_id"] = owner_user_id
                if owner_names[owner_user_id]:
                    entry["owner_display_name"] = owner_names[owner_user_id]
            entry["source"] = "bundled" if definition_id in template_ids else "user"
            if entry["source"] == "bundled":
                entry["lifecycle"] = "reseeded"

            definition = await repositories.definitions.get(definition_id)
            tool_names: list[str] = []
            if definition is not None:
                for step in definition.steps:
                    tool_names.extend(getattr(step, "tools", None) or [])
            effects: list[str] = []
            effect_tools: list[str] = []
            for name in dict.fromkeys(tool_names):
                tool = engine.tools.get(name) if engine is not None else None
                tool_effect = getattr(tool, "effect", None) if tool is not None else None
                effects.append(tool_effect or "unknown")
                if tool_effect != "read_only":
                    effect_tools.append(name)
            if any(e in ("mutating", "unknown") for e in effects):
                entry["run_effect"] = "mutating"
                entry["effect_tools"] = effect_tools
            else:
                entry["run_effect"] = "read_only"
            out[definition_id] = entry
        return out

    @router.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
    async def get_workflow(
        workflow_id: str, scope: OrgScope = Depends(_org_scope)
    ) -> WorkflowDefinition:
        await _visible_definition(workflow_id, scope)
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        return definition

    @router.delete("/workflows/{workflow_id}")
    async def delete_workflow(
        workflow_id: str,
        force: bool = False,
        user: UserIdentity = Depends(require_roles(*ORG_ADMIN_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Hard-delete a workflow definition and cascade to its run history
        (instances + their step_executions). Admin/Designer only.

        409 if any instance is non-terminal (pending/running/paused) — kill or
        wait first, so the engine never has its rows deleted mid-run.

        Returns counts: `{deleted_workflow, deleted_instances, deleted_steps}`.
        Audit entries are immutable and preserved (same as instance deletes).

        Note: this does not unregister an already-running in-process trigger for
        the workflow (filesystem / schedule / webhook / email) — restart the
        server to fully clear it. Bundled examples are re-seeded on restart by the
        trigger orchestrator, so deleting one only clears it until the next boot."""
        await _visible_definition(workflow_id, scope)
        definition_org = await repositories.definitions.org_of(workflow_id)
        if await repositories.definitions.get(workflow_id) is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

        instances = await repositories.instances.list_by_workflow(workflow_id)
        # Refuse while any run is live: deleting rows out from under the engine
        # breaks its next state write, and a PAUSED run would become
        # unresumable. Kill or wait first.
        terminal = {
            WorkflowInstanceState.COMPLETED,
            WorkflowInstanceState.FAILED,
            WorkflowInstanceState.KILLED,
        }
        live = [i for i in instances if i.state not in terminal]
        if live:
            states = sorted({i.state.value for i in live})
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Workflow {workflow_id!r} has {len(live)} non-terminal instance(s) "
                    f"(states: {', '.join(states)}). Kill or wait for them, then delete."
                ),
            )
        # Run-history containment (external review round 3 §8): a plain
        # delete must not silently cascade audit/run evidence. Refuse when
        # history exists unless the caller explicitly passes force=true —
        # until soft-delete/archive lands (G21). Definitions with no run
        # history delete freely (the common case: an unused draft).
        if instances and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Workflow {workflow_id!r} has {len(instances)} run(s) of history. "
                    f"Deleting cascades their audit trail — pass force=true to confirm, "
                    f"or export first. (Soft-delete/archive is tracked as G21.)"
                ),
            )
        instance_ids = [i.id for i in instances]
        deleted_steps = (
            await repositories.steps.delete_by_instances(instance_ids) if instance_ids else 0
        )
        deleted_instances = 0
        for instance_id in instance_ids:
            if await repositories.instances.delete(instance_id):
                deleted_instances += 1

        await repositories.definitions.delete(workflow_id)
        await _audit_admin_action(
            user,
            "workflow_deleted",
            detail={
                "workflow_id": workflow_id,
                "deleted_instances": deleted_instances,
                "deleted_steps": deleted_steps,
                **_bypass(scope, definition_org or "default"),
            },
        )
        return {
            "deleted_workflow": workflow_id,
            "deleted_instances": deleted_instances,
            "deleted_steps": deleted_steps,
        }

    @router.get("/workflows/{workflow_id}/export")
    async def export_workflow(
        workflow_id: str,
        format: str = "json",
        scope: OrgScope = Depends(_org_scope),
    ) -> Response:
        await _visible_definition(workflow_id, scope)
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
        org_id: str | None = Query(default=None),
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
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
        await repositories.definitions.save(definition, **await _attribution(user, scope, org_id))
        return {"status": "imported", "workflow_id": definition.id}

    @router.post("/workflows/{workflow_id}/run")
    async def run_workflow(
        workflow_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        await _visible_definition(workflow_id, scope)
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

    @router.post("/workflows/{workflow_id}/run-batch")
    async def run_workflow_batch(
        workflow_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Fire a workflow once per row of a batch (C8.1).

        Body: a JSON array of trigger-payload objects (the GUI parses an uploaded
        CSV / pasted JSON into this). Runs with bounded concurrency so a large
        batch doesn't stampede Bedrock; one row's failure is isolated, not fatal.
        Awaited synchronously like the single run, so results carry final state.

        Returns `{submitted, succeeded, failed, results}` where each result is
        `{index, ok, instance_id?, state?, error?}` in input order."""
        await _visible_definition(workflow_id, scope)
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Run requires a WorkflowEngine bound to the API."
            )
        eng = engine  # narrowed; capture for the closure below
        definition = await repositories.definitions.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")

        body = await request.body()
        text = body.decode("utf-8") if body else ""
        try:
            payloads = json.loads(text) if text.strip() else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payloads, list):
            raise HTTPException(
                status_code=400, detail="Batch body must be a JSON array of payload objects"
            )
        if not payloads:
            raise HTTPException(status_code=400, detail="Batch is empty — provide at least one row")
        if len(payloads) > _BATCH_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Batch too large: {len(payloads)} rows (max {_BATCH_MAX}).",
            )
        if any(not isinstance(p, dict) for p in payloads):
            raise HTTPException(status_code=400, detail="Every batch row must be a JSON object")

        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _run_one(index: int, payload: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                # Isolate one row's failure so the rest of the batch still runs.
                try:
                    inst = await eng.run(definition, trigger_payload=payload)
                except Exception:
                    # P2: the exception text can carry raw (tool/provider
                    # output). Report failure; the detail endpoint has the rest.
                    logger.warning("run-batch row %s failed", index, exc_info=True)
                    return {"index": index, "ok": False, "error": _REDACTED_GRANT_ONLY}
                return {
                    "index": index,
                    "ok": True,
                    "instance_id": inst.id,
                    "state": inst.state.value,
                }

        results = await asyncio.gather(*(_run_one(i, p) for i, p in enumerate(payloads)))
        succeeded = sum(1 for r in results if r["ok"])
        return {
            "workflow_id": workflow_id,
            "submitted": len(payloads),
            "succeeded": succeeded,
            "failed": len(payloads) - succeeded,
            "results": results,
        }

    @router.post("/workflows/{workflow_id}/dry-run")
    async def dry_run_workflow(
        workflow_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Run a workflow once in a sandbox (C6.1): a `MockWorld` (no real file /
        database / messaging side effects) and a tool catalog with the external
        tools (email / connector / browser) removed, but **live Bedrock** so the
        agent reasons for real — "sandbox the world, keep the brain". The
        instance is persisted and tagged `dry_run` so the canvas can follow it.

        The tag keeps dry runs out of the per-workflow cost series, the C6.2
        pre-run estimate, and the monitoring error-rate alert. By-model /
        by-day cost views and Prometheus counters keep them deliberately —
        dry-run tokens are real Bedrock spend and real system activity.

        Browser-automation workflows are rejected (a real browser can't be
        sandboxed yet)."""
        await _visible_definition(workflow_id, scope)
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
        # Learned memory in a dry run: snapshot-read, discard-write. The real
        # store is COPIED into an ephemeral scratch DB so recall (G10) behaves
        # exactly as production would, while observe writes land in the copy
        # and vanish with the temp dir — "sandbox the world, keep the brain".
        with tempfile.TemporaryDirectory(prefix="dry-run-memory-") as scratch:
            scratch_memory: LearnedMemoryService | None = None
            if engine.learned_memory is not None:
                scratch_db = Path(scratch) / "learned.db"
                real_db = engine.learned_memory.db_path
                if real_db.is_file():
                    try:
                        shutil.copyfile(real_db, scratch_db)
                    except OSError:
                        logger.warning(
                            "Dry-run: couldn't snapshot the learned-memory store; "
                            "recall will see an empty store.",
                            exc_info=True,
                        )
                scratch_memory = LearnedMemoryService(
                    engine.bedrock,
                    scratch_db,
                    model_id=engine.learned_memory.model_id,
                )
            dry_engine = dataclasses.replace(
                engine,
                world=mock_world(),
                tools=ToolCatalog(sandbox_tools),
                learned_memory=scratch_memory,
                dry_run=True,
            )
            try:
                instance = await dry_engine.run(definition, trigger_payload=payload)
            finally:
                if scratch_memory is not None:
                    scratch_memory.close()
        # The engine stamps `dry_run` onto the context at run start
        # (`WorkflowContext.dry_run`), so it is already persisted and
        # survives a later context rewrite. Kept as a belt-and-braces write
        # for instances whose context somehow lacks it.
        if not (instance.context or {}).get("dry_run"):
            instance.context = {**(instance.context or {}), "dry_run": True}
            await repositories.instances.update(instance)
        return {
            # The RUN's outcome, not the request's. It said "completed"
            # unconditionally, so a dry run that failed reported success at a
            # glance and contradicted the `state` beside it.
            "status": instance.state.value,
            "instance_id": instance.id,
            "state": instance.state.value,
            "dry_run": True,
            # P2: a failed dry run's error carries whatever the step raised —
            # tool/provider text included. The full error is on the grant-gated
            # instance-detail endpoint; this surface only reports failure.
            "error": redact_error(instance.error, admin=False),
            "sandbox": "MockWorld; external tools (email/connector/browser) disabled; live Bedrock",
        }

    def _instance_summary(i: WorkflowInstance) -> dict[str, Any]:
        """A list/dashboard row — NEVER the full execution trace (external
        review 2026-08-01). Omits `context` (step outputs, tool_calls,
        echoed output_text) AND `trigger_payload` (raw mail content);
        surfaces only identity, state, timestamps, and the non-sensitive
        cost/token totals. The detail endpoint serves the (redacted) full
        instance."""
        ctx = i.context or {}
        return {
            "id": i.id,
            "workflow_id": i.workflow_id,
            "org_id": i.org_id,
            "state": i.state.value,
            # Error text is raw (F2); the bulk list never shows it — the full
            # error is on the grant-gated detail endpoint.
            "error": redact_error(i.error, admin=False),
            "created_at": _iso(i.created_at),
            "started_at": _iso(i.started_at),
            "completed_at": _iso(i.completed_at),
            "total_tokens": ctx.get("total_tokens"),
            "total_cost_usd": ctx.get("total_cost_usd"),
        }

    @router.get("/workflow-instances")
    async def list_instances(
        workflow_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        scope: OrgScope = Depends(_org_scope),
    ) -> list[dict[str, Any]]:
        if workflow_id:
            items = await repositories.instances.list_by_workflow(workflow_id, org_id=scope.org_id)
        else:
            items = await repositories.instances.list_recent(limit=1000, org_id=scope.org_id)
        if state:
            items = [i for i in items if i.state.value == state]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [_instance_summary(i) for i in items[: max(1, min(limit, 200))]]

    @router.get("/workflow-instances/{instance_id}")
    async def get_instance(
        instance_id: str,
        user: UserIdentity = Depends(require_roles(*ANY_ROLE)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        instance = await _visible_instance(instance_id, scope)
        steps = await repositories.steps.list_by_instance(instance_id)
        raw_ok = await _raw_reader_for_org(user, instance.org_id)
        kinds = ("tool_calls", "output_text", "trigger_payload", "recall", "error")
        # F8: audit the ATTEMPT before any fetch; the release DECISION lands
        # after, reflecting what the vault actually returned.
        request_id, reason = await begin_raw_release(
            repositories,
            raw_ok=raw_ok,
            surface=SURFACE_DETAIL,
            actor_id=user.sub,
            instance_id=instance_id,
            kinds=kinds,
        )
        instance_dump = instance.model_dump()
        step_dumps = [s.model_dump() for s in steps]
        released = False
        if request_id is not None:
            # Grant-holder: restore raw from the vault (TG3b.3). A no-op under
            # the default dark dual-write; under the flip it re-merges the
            # projected operational rows.
            #
            # R15 finding 1: the merge helpers raise `RawTraceUnavailable` on
            # a lookup timeout or an undecryptable payload and nothing here
            # caught it — HTTP 500, with `..._access_attempted` recorded and
            # no release decision at all. A retrieval failure is an outcome:
            # complete the decision, serve the projection.
            try:
                instance_dump["trigger_payload"] = await rehydrator.merge_trigger(
                    org_id=instance.org_id,
                    instance_id=instance_id,
                    safe_trigger=instance_dump.get("trigger_payload") or {},
                )
                instance_dump["error"] = await rehydrator.merge_error(
                    org_id=instance.org_id,
                    instance_id=instance_id,
                    step_attempt_id=None,
                    safe_error=instance_dump.get("error"),
                )
                for sd in step_dumps:
                    if isinstance(sd.get("output"), dict):
                        sd["output"] = await rehydrator.merge_output(
                            org_id=instance.org_id,
                            instance_id=instance_id,
                            step_attempt_id=sd["id"],
                            safe_output=sd["output"],
                            projector_version=sd.get("projector_version"),
                        )
                    sd["error"] = await rehydrator.merge_error(
                        org_id=instance.org_id,
                        instance_id=instance_id,
                        step_attempt_id=sd["id"],
                        safe_error=sd.get("error"),
                    )
                # instance.context echoes the trigger + each step's output (projected
                # at rest under the flip) — restore those too so the grant-holder
                # response is genuinely complete (not just the top-level fields).
                ctx = instance_dump.get("context")
                if isinstance(ctx, dict):
                    if isinstance(ctx.get("trigger"), dict):
                        ctx["trigger"] = await rehydrator.merge_trigger(
                            org_id=instance.org_id,
                            instance_id=instance_id,
                            safe_trigger=ctx["trigger"],
                        )
                    ctx_steps = ctx.get("steps")
                    if isinstance(ctx_steps, dict):
                        latest_attempt = {s.step_id: s.id for s in steps}
                        stamp_by_step = {s.step_id: s.projector_version for s in steps}
                        for sid, out in list(ctx_steps.items()):
                            if isinstance(out, dict) and sid in latest_attempt:
                                ctx_steps[sid] = await rehydrator.merge_output(
                                    org_id=instance.org_id,
                                    instance_id=instance_id,
                                    step_attempt_id=latest_attempt[sid],
                                    safe_output=out,
                                    projector_version=stamp_by_step.get(sid),
                                )
                # Which kinds actually came back: if a marker still remains after
                # merge, at least one vault object was missing (partial/failed).
                complete = not has_redaction_marker(instance_dump) and not any(
                    has_redaction_marker(sd) for sd in step_dumps
                )
            except RawTraceUnavailable as exc:
                # Record what actually happened, then fall through to the
                # projected response with the reason attached.
                logger.warning("instance-detail recovery failed: %s", exc)
                _audit_ok, reason = await commit_raw_release(
                    repositories,
                    request_id=request_id,
                    surface=SURFACE_DETAIL,
                    actor_id=user.sub,
                    instance_id=instance_id,
                    returned_kinds=(),
                    withheld_kinds=kinds,
                )
                return {
                    "instance": redact_tool_data(instance.model_dump(), False, kind="instance"),
                    "steps": [
                        redact_tool_data(s.model_dump(), False, kind="step_row") for s in steps
                    ],
                    "raw_included": False,
                    "redaction_reason": reason or "retrieval_failed",
                }
            audit_ok, reason = await commit_raw_release(
                repositories,
                request_id=request_id,
                surface=SURFACE_DETAIL,
                actor_id=user.sub,
                instance_id=instance_id,
                returned_kinds=kinds if complete else (),
                withheld_kinds=() if complete else kinds,
            )
            # Release the (possibly partial) raw only after release_decided
            # committed; a failed decision audit fails closed to projected.
            released = audit_ok
        body: dict[str, Any] = {
            # released keeps whatever merged (raw + any residual markers where
            # retrieval failed); raw_included is honest — full release only.
            "instance": redact_tool_data(instance_dump, released, kind="instance"),
            "steps": [redact_tool_data(sd, released, kind="step_row") for sd in step_dumps],
            "raw_included": released and reason is None,
        }
        if reason is not None:
            body["redaction_reason"] = reason
        return body

    @router.post("/workflow-instances/{instance_id}/pause")
    async def pause_instance(
        instance_id: str,
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        instance = await _visible_instance(instance_id, scope)
        await _note_bypass(scope, instance.org_id, "instance_pause_requested", instance_id)
        if instance.state != WorkflowInstanceState.RUNNING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot pause: instance is {instance.state.value}",
            )
        instance.state = WorkflowInstanceState.PAUSED
        await repositories.instances.update(instance)
        # Same hole as kill, found in the same pass. The engine audits
        # `workflow_paused` for a pause IT decides (budget); an operator
        # pause is written here and the engine never sees it.
        await _audit_admin_action(user, "workflow_paused", instance_id=instance_id)
        return {"status": "pause_requested", "instance_id": instance_id}

    @router.post("/workflow-instances/{instance_id}/resume")
    async def resume_instance(
        instance_id: str,
        _: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Resume requires a WorkflowEngine bound to the API."
            )
        instance = await _visible_instance(instance_id, scope)
        await _note_bypass(scope, instance.org_id, "instance_resume_requested", instance_id)
        if instance.state != WorkflowInstanceState.PAUSED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume: instance is {instance.state.value}",
            )
        _reject_dry_run(instance, "resume")
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
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Retry requires a WorkflowEngine bound to the API."
            )
        instance = await _visible_instance(instance_id, scope)
        await _note_bypass(scope, instance.org_id, "instance_retry_requested", instance_id)
        if instance.state != WorkflowInstanceState.FAILED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot retry: instance is {instance.state.value}",
            )
        _reject_dry_run(instance, "retry")
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
        # Its OWN action, not `workflow_paused`: this FAILED -> PAUSED hop is
        # a retry decision, and the engine's subsequent `workflow_resumed`
        # would otherwise be the only trace — a trail reading "resumed" with
        # no record that a human retried a failed run.
        await _audit_admin_action(user, "workflow_retried", instance_id=instance_id)
        task = asyncio.create_task(engine.resume(definition, instance_id))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return {"status": "retry_started", "instance_id": instance_id}

    @router.post("/workflow-instances/{instance_id}/fork")
    async def fork_instance(
        instance_id: str,
        request: Request,
        _: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
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
        source = await _visible_instance(instance_id, scope)
        await _note_bypass(scope, source.org_id, "instance_fork_requested", instance_id)
        _reject_dry_run(source, "fork")
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
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        instance = await _visible_instance(instance_id, scope)
        await _note_bypass(scope, instance.org_id, "instance_kill_requested", instance_id)
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
        # The ENGINE audits `workflow_killed` when it observes a kill mid-run
        # (`_KillRequested`). This path never reaches the engine — it writes
        # the terminal state itself — so without this the transition left no
        # record at all. Found 2026-09-19 while bulk-killing 171 recovered
        # orphans: 171 rows to a terminal state with nothing saying who or
        # why is indistinguishable from tampering when someone reads it back.
        # Same action name as the engine's, so a consumer does not need to
        # know which path ran; `actor_type` tells them.
        await _audit_admin_action(user, "workflow_killed", instance_id=instance_id)
        return {"status": "kill_requested", "instance_id": instance_id}

    @router.delete("/workflow-instances/{instance_id}", status_code=204)
    async def delete_instance(
        instance_id: str,
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
    ) -> Response:
        """Hard-delete a terminal instance + its step_executions.

        Audit entries referencing the instance are intentionally left in
        place: the audit log is append-only by design, so the history
        of what happened survives the cleanup of what currently exists.

        Refuses on non-terminal states (running / pending / paused) — kill
        first if the operator wants to stop a live run.
        """
        instance = await _visible_instance(instance_id, scope)
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
        deleted_steps = await repositories.steps.delete_by_instance(instance_id)
        await repositories.instances.delete(instance_id)
        await _audit_admin_action(
            user,
            "instance_deleted",
            instance_id=instance_id,
            detail={
                "workflow_id": instance.workflow_id,
                "deleted_steps": deleted_steps,
                **_bypass(scope, instance.org_id),
            },
        )
        return Response(status_code=204)

    @router.delete("/workflow-instances")
    async def delete_instances_bulk(
        state: list[str] = Query(default=...),
        workflow_id: str | None = Query(default=None),
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
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
            list(requested), workflow_id=workflow_id, org_id=scope.org_id
        )
        deleted_steps = await repositories.steps.delete_by_instances(deleted_ids)
        await _audit_admin_action(
            user,
            "instances_bulk_deleted",
            detail={
                "states": sorted(requested),
                "workflow_id": workflow_id,  # null = platform-wide
                "deleted_instances": len(deleted_ids),
                "deleted_steps": deleted_steps,
            },
        )
        return {
            "deleted_instances": len(deleted_ids),
            "deleted_steps": deleted_steps,
        }

    async def _raw_reader_for_org(user: UserIdentity, target_org: str | None) -> bool:
        """Whether `user` may read RAW tool payloads of a resource in
        `target_org`. The privilege is a per-user GRANT distinct from
        administration (docs/TRACE_GOVERNANCE_PLAN.md §2, TG1) — NOT a role.
        An org-scoped grant covers only its org; a platform-wide grant covers
        any (incl. `target_org is None`, the cross-org/global surfaces). An
        unresolvable user, or no covering grant, reads only projected traces."""
        row = await repositories.users.get_by_identity(current_issuer(), user.sub)
        if row is None:
            return False
        return (
            await grant_service.covering(principal_id=row.id, target_org=target_org)
        ) is not None

    def _project_audit(entries: list[AuditEntry], raw_ok: bool) -> list[AuditEntry]:
        """Below-grant projection of audit details (a response-layer
        projection; storage is unchanged)."""
        if raw_ok:
            return entries
        return [
            e.model_copy(update={"detail": project_audit_detail(e.action, e.detail)})
            for e in entries
        ]

    async def _release_audit(
        entries: list[AuditEntry], *, actor_id: str, instance_id: str | None
    ) -> tuple[list[AuditEntry], list[str], list[str], set[str]]:
        """Restore vaulted audit details for a grant holder.

        R12 finding 1: the audit endpoints handed back the STORED detail.
        That was right while at rest held the raw; after the at-rest
        tightening, stored IS the projection, so a grant holder got
        `{"_withheld_keys": true}` and the release log still said `released`.

        Returns the entries plus the kinds actually returned/withheld, so the
        caller commits the outcome it achieved rather than the one it
        intended. Audit is a VAULT-FETCH surface now, which is exactly the
        case `decide_raw_release` documents itself as not covering.
        """
        restored: list[AuditEntry] = []
        # Per-ENTRY outcome, not just a count: `/api/escalations` reports
        # `raw_included` per row, and reporting the request-level verdict
        # there is how it claimed a release it had not made (R13 finding 1).
        recovered_ids: set[str] = set()
        recovered = failed = 0
        for entry in entries:
            if entry.projector_version is None:
                # Never written as a projection, so the stored detail IS the
                # complete detail and the reader already has it. That counts
                # as COMPLETE, not as "no recovery attempted".
                #
                # R14 finding 3: it was added to `recovered_ids` but did NOT
                # increment `recovered`, so a mixed response — one complete
                # inline entry, one unavailable vaulted entry — returned the
                # first row's full content with `raw_included: true` while
                # the request outcome said `retrieval_failed` with
                # `released_kinds: []`. The row-level and request-level
                # accounts contradicted each other about the same response.
                recovered += 1
                recovered_ids.add(entry.id)
                restored.append(entry)
                continue
            if entry.workflow_instance_id is None:
                # Stamped but instance-less: the vault is instance-scoped, so
                # there is nowhere to recover from. Incomplete, and said so.
                failed += 1
                restored.append(entry)
                continue
            # Resolve the org PER ENTRY, not once for the request: the
            # global /audit list spans orgs, so a single org would decrypt
            # against the wrong AEAD identity (or the wrong tenant's key).
            owner = await repositories.instances.get(entry.workflow_instance_id)
            if owner is None:
                failed += 1
                restored.append(entry)
                continue
            try:
                full = await rehydrator.rehydrate_audit_detail(
                    purpose=SURFACE_AUDIT,
                    org_id=owner.org_id,
                    instance_id=entry.workflow_instance_id,
                    audit_entry_id=entry.id,
                    action=entry.action,
                    stored_detail=entry.detail,
                    projector_version=entry.projector_version,
                )
            except RawTraceUnavailable:
                # Fail CLOSED per entry: hand back the projection, and let the
                # caller report `partial` rather than claim a full release.
                failed += 1
                restored.append(entry)
                continue
            recovered += 1
            recovered_ids.add(entry.id)
            restored.append(entry.model_copy(update={"detail": full}))
        returned = ["audit_detail"] if recovered else []
        withheld = ["audit_detail"] if failed else []
        return restored, returned, withheld, recovered_ids

    async def _audit_response(
        entries: list[AuditEntry],
        *,
        raw_ok: bool,
        actor_id: str,
        instance_id: str | None,
    ) -> list[AuditEntry]:
        """Begin the access, fetch from the vault, THEN commit the outcome
        that actually occurred — the two-phase shape `begin_raw_release`
        documents for vault-fetch surfaces."""
        request_id, _reason = await begin_raw_release(
            repositories,
            raw_ok=raw_ok,
            surface=SURFACE_AUDIT,
            actor_id=actor_id,
            instance_id=instance_id,
            kinds=("audit_detail",),
        )
        if request_id is None:
            # Below grant, or the attempt audit failed. Either way: project.
            return _project_audit(entries, False)
        restored, returned, withheld, _ids = await _release_audit(
            entries, actor_id=actor_id, instance_id=instance_id
        )
        audit_ok, _ = await commit_raw_release(
            repositories,
            request_id=request_id,
            surface=SURFACE_AUDIT,
            actor_id=actor_id,
            instance_id=instance_id,
            returned_kinds=returned,
            withheld_kinds=withheld,
        )
        # Fail closed: a release we could not record is a release we do not make.
        return restored if audit_ok else _project_audit(entries, False)

    @router.get(
        "/workflow-instances/{instance_id}/audit",
        response_model=list[AuditEntry],
    )
    async def list_instance_audit(
        instance_id: str,
        user: UserIdentity = Depends(require_roles(*ANY_ROLE)),
        scope: OrgScope = Depends(_org_scope),
    ) -> list[AuditEntry]:
        instance = await _visible_instance(instance_id, scope)
        raw_ok = await _raw_reader_for_org(user, instance.org_id)
        entries = await repositories.audit.list_by_instance(instance_id)
        return await _audit_response(
            entries, raw_ok=raw_ok, actor_id=user.sub, instance_id=instance_id
        )

    @router.get("/audit", response_model=list[AuditEntry])
    async def list_recent_audit(
        limit: int = 100,
        instance_id: str | None = None,
        user: UserIdentity = Depends(require_roles(*ANY_ROLE)),
        scope: OrgScope = Depends(_org_scope),
    ) -> list[AuditEntry]:
        """Recent audit entries, optionally scoped to one instance. Before
        `instance_id` was accepted here, passing it was silently ignored and
        the global list came back — misleading for audit consumers."""
        if instance_id is not None:
            instance = await _visible_instance(instance_id, scope)
            raw_ok = await _raw_reader_for_org(user, instance.org_id)
            entries = await repositories.audit.list_by_instance(instance_id)
            return await _audit_response(
                entries[: min(limit, 500)],
                raw_ok=raw_ok,
                actor_id=user.sub,
                instance_id=instance_id,
            )
        # The global list spans orgs — only a PLATFORM-WIDE grant reads raw
        # here (an org-scoped grant covers only its own org's entries), which
        # `covering(target_org=None)` returns exactly.
        raw_ok = await _raw_reader_for_org(user, None)
        # R13 self-audit: this branch still had the round-12 defect after the
        # other two were fixed — a platform-wide grant holder got the STORED
        # (projected) entries while the log recorded a release. Fixing two of
        # three call sites is the half-built-path pattern the round-12 return
        # was about, so it goes through the same responder.
        return await _audit_response(
            await repositories.audit.list_recent(limit=min(limit, 500), org_id=scope.org_id),
            raw_ok=raw_ok,
            actor_id=user.sub,
            instance_id=None,
        )

    if webhook_registry is not None:
        registry = webhook_registry  # narrowed for the closure

        @router.post("/triggers/webhook/{trigger_id}")
        async def fire_webhook(trigger_id: str, request: Request) -> dict[str, Any]:
            """Fire a webhook trigger. Exempt from user auth (senders can't
            carry a user token), so security is HMAC (G2): when the trigger's
            YAML config sets `secret_name`, the request must carry a GitHub-
            style `X-Hub-Signature-256: sha256=<hex hmac-sha256(secret, body)>`
            header — 401 otherwise, 503 (fail closed) if the named secret can't
            be loaded. Triggers without `secret_name` accept unsigned posts:
            the local-dev path; don't deploy one reachable from the internet."""
            raw = await request.body()

            secret_name = registry.secret_name(trigger_id)
            if secret_name is not None:
                if secret_store is None:
                    logger.error(
                        "Webhook %r requires HMAC but the API has no SecretStore; refusing.",
                        trigger_id,
                    )
                    raise HTTPException(status_code=503, detail="Webhook verification unavailable")
                try:
                    secret = await secret_store.get(secret_name)
                except SecretNotFoundError:
                    logger.error(
                        "Webhook %r: HMAC secret %r not found in SecretStore; refusing.",
                        trigger_id,
                        secret_name,
                    )
                    raise HTTPException(
                        status_code=503, detail="Webhook verification unavailable"
                    ) from None
                signature = request.headers.get("X-Hub-Signature-256", "")
                expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                if not signature.startswith("sha256=") or not hmac.compare_digest(
                    expected, signature[len("sha256=") :]
                ):
                    logger.warning("Webhook %r: rejected request with bad/missing HMAC", trigger_id)
                    raise HTTPException(status_code=401, detail="Invalid webhook signature")

            try:
                payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

            fired = await registry.fire(trigger_id, payload)
            if not fired:
                raise HTTPException(
                    status_code=404, detail=f"No webhook trigger registered for {trigger_id!r}"
                )
            return {"status": "fired", "trigger_id": trigger_id}

    async def _instance_org(instance_id: str | None, cache: dict[str, str | None]) -> str | None:
        if instance_id is None:
            return None
        if instance_id not in cache:
            instance = await repositories.instances.get(instance_id)
            cache[instance_id] = instance.org_id if instance else None
        return cache[instance_id]

    @router.get("/escalations")
    async def list_escalations(
        state: str = "pending",
        limit: int = 50,
        user: UserIdentity = Depends(require_roles(*ANY_ROLE)),
        scope: OrgScope = Depends(_org_scope),
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
        if scope.org_id is not None:
            # ROLES_PLAN §7.6b: escalations are visible to their instance's
            # org only; instance-less ones are platform-operator data.
            org_cache: dict[str, str | None] = {}
            scoped = []
            for e in requested:
                if await _instance_org(e.workflow_instance_id, org_cache) == scope.org_id:
                    scoped.append(e)
            requested = scoped
        rows = requested[: max(1, min(limit, 200))]
        # P2 (re-review finding 2): `reason` + `context` come from
        # `request_human_review`, whose arguments an agent writes while reading
        # hostile third-party content — they are raw, not operator metadata, and
        # were previously returned in full to any Org Viewer. Grant-gate them.
        # R13 finding 1: this returned `e.detail.get("reason")` — the STORED
        # detail, which since the at-rest tightening has reason/context
        # withheld. A grant holder got `reason: null` with
        # `raw_included: true`, while the SAME entry's full detail came back
        # fine from the audit endpoint. Escalation is a vault-fetch surface
        # too, so it uses attempt -> retrieve -> record-what-happened.
        reason_code: str | None = None
        recovered_ids: set[str] = set()
        if rows:
            request_id, reason_code = await begin_raw_release(
                repositories,
                raw_ok=await _raw_reader_for_org(user, scope.org_id),
                surface=SURFACE_ESCALATION,
                actor_id=user.sub,
                instance_id=None,
                kinds=("escalation_reason", "escalation_context"),
            )
            if request_id is not None:
                restored, returned, withheld, recovered_ids = await _release_audit(
                    rows, actor_id=user.sub, instance_id=None
                )
                audit_ok, reason_code = await commit_raw_release(
                    repositories,
                    request_id=request_id,
                    surface=SURFACE_ESCALATION,
                    actor_id=user.sub,
                    instance_id=None,
                    returned_kinds=returned,
                    withheld_kinds=withheld,
                )
                if audit_ok:
                    rows = restored
                else:
                    # A release we could not record is a release we do not make.
                    recovered_ids = set()
        return [
            {
                "id": e.id,
                "instance_id": e.workflow_instance_id,
                "step_id": e.step_id,
                "actor_id": e.actor_id,
                # Escalation `reason` AND `context` are MODEL-AUTHORED free-form
                # (RequestHumanReviewTool), so both are raw by taint — gated
                # whole on the grant, never projected against the platform
                # `context` schema (G-Trace-Review-4 F2: a model can spell
                # declared context keys and pass the engine-computed validators).
                "reason": (
                    e.detail.get("reason") if e.id in recovered_ids else _REDACTED_GRANT_ONLY
                ),
                "context": (
                    e.detail.get("context") if e.id in recovered_ids else _REDACTED_GRANT_ONLY
                ),
                "created_at": e.timestamp.isoformat(),
                "resolved": e.id in resolved_ids,
                # PER ROW, from what was actually recovered for THIS entry.
                "raw_included": e.id in recovered_ids,
                # R14 finding 3: the request-level `reason_code` was attached
                # to EVERY row, so a successfully returned escalation carried
                # a failure explanation. A row that got its raw needs no
                # explanation; one that did not gets the request's.
                **(
                    {"redaction_reason": reason_code}
                    if reason_code and e.id not in recovered_ids
                    else {}
                ),
            }
            for e in rows
        ]

    @router.post("/escalations/{escalation_id}/resolve")
    async def resolve_escalation(
        escalation_id: str,
        body: dict[str, Any],
        user: UserIdentity = Depends(require_roles(*ORG_WRITE_ROLES)),
        scope: OrgScope = Depends(_org_scope),
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
        escalation_org = await _instance_org(requested.workflow_instance_id, {})
        if scope.org_id is not None and escalation_org != scope.org_id:
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
                    **(_bypass(scope, escalation_org) if escalation_org else {}),
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
        org_id: str | None = None,
        scope: OrgScope = Depends(_org_scope),
    ) -> list[dict[str, Any]]:
        effective_org = scope.org_id if scope.org_id is not None else org_id
        rows = await cost_service.by_workflow(_parse_since(since), org_id=effective_org)
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
        org_id: str | None = None,
        scope: OrgScope = Depends(_org_scope),
    ) -> list[dict[str, Any]]:
        effective_org = scope.org_id if scope.org_id is not None else org_id
        rows = await cost_service.by_model(_parse_since(since), org_id=effective_org)
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
        org_id: str | None = None,
        scope: OrgScope = Depends(_org_scope),
    ) -> list[dict[str, Any]]:
        effective_org = scope.org_id if scope.org_id is not None else org_id
        rows = await cost_service.by_day(_parse_since(since), org_id=effective_org)
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
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Pre-run cost context for the Run dialog (C6.2): per-agentic-step model
        rates, the budget policy, and the average cost/tokens per run from
        history (null when the workflow hasn't run yet)."""
        await _visible_definition(workflow_id, scope)
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
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Per-agentic-step tool capability boundary (C6.3): which catalog tools
        each step can use vs is denied, and why. Uses the same layer
        intersection the engine enforces (system -> workflow -> step)."""
        await _visible_definition(workflow_id, scope)
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
        user: UserIdentity = Depends(require_roles(*ANY_ROLE)),
        scope: OrgScope = Depends(_org_scope),
    ) -> dict[str, Any]:
        """Forensic view of one step in a run (C6.4): for an agent step, what it
        was asked, the tools it called (args + results), tokens/cost, and the
        memory hash in effect; for a deterministic step, its function + output.
        Assembled from the step execution + the step's audit slice."""
        instance = await _visible_instance(instance_id, scope)
        execs = [
            e
            for e in await repositories.steps.list_by_instance(instance_id)
            if e.step_id == step_id
        ]
        if not execs:
            raise HTTPException(
                status_code=404, detail=f"Step {step_id!r} not found in instance {instance_id!r}"
            )
        # EXECUTION_SEMANTICS §3a: "current logical-step state = the latest
        # attempt row (max `attempt`)". This read `execs[-1]` — last by
        # `started_at`, a PROXY that agrees only while attempt numbers rise
        # with time. They did not, between 2026-08-01 and 2026-09-19, when
        # the resume path reused attempt 1. The engine's own
        # `_rehydrate_context` already used max(attempt); two readers with
        # two rules is the M3 class, so this one now states the rule.
        # Tie-break on start time, because the attempt number alone is not
        # a total order over the rows that ALREADY exist: two `triage` rows
        # share attempt 1 on the instances the old resume path produced.
        # Verified live — with a bare `max(... .attempt)` those instances
        # reported the CANCELLED attempt for a step that had completed,
        # since max() keeps the first maximum it meets. Neither answer is
        # right on ambiguous data; the later one is the useful one, and it
        # is what the previous proxy returned, so correcting the rule does
        # not silently change the answer on historical rows.
        exe = max(
            execs, key=lambda e: (e.attempt, e.started_at or datetime.min.replace(tzinfo=UTC))
        )
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
        raw_ok = await _raw_reader_for_org(user, instance.org_id)
        # R17 finding: `error` was recovered but never declared, so the
        # release decision could not name it as withheld. A surface must
        # declare every kind it tries to release.
        exp_kinds = ("tool_calls", "output_text", "error")
        # F8: attempt-audit before the vault fetch; the release decision lands
        # after and reflects whether the raw actually came back.
        request_id, reason = await begin_raw_release(
            repositories,
            raw_ok=raw_ok,
            surface=SURFACE_EXPLAIN,
            actor_id=user.sub,
            instance_id=instance_id,
            kinds=exp_kinds,
        )
        audit_tcs = [a for a in audit if a.action == "tool_call"]
        # Under the flip the tool_call audit entries are projected at rest, so a
        # grant-holder's raw input/result comes from THIS step-attempt's vaulted
        # output.tool_calls (rehydrated + decrypted, in call order). Under the
        # default dark dual-write the audit detail still holds raw and
        # merge_output is a no-op — either way `raw_tcs[i]` aligns with the
        # i-th tool_call audit entry.
        raw_tcs: list[Any] = []
        released = False
        merged_error = exe.error
        # What the response is BUILT from. Replaced by the recovered output
        # only after a successful release; a failed retrieval or a failed
        # release-decision audit keeps the projection.
        presented: dict[str, Any] = output
        if request_id is not None:
            # R16 self-audit: the SAME defect R15 finding 1 described, on a
            # surface the reviewer did not test. `merge_output` raises
            # `RawTraceUnavailable` on a lookup timeout or an undecryptable
            # payload, and nothing here caught it — HTTP 500 between
            # `begin_raw_release` and `commit_raw_release`, so the attempt
            # was recorded and the decision never was. Found by enumerating
            # the CALLERS of the recovery helpers rather than the helpers
            # themselves (ledger R-g).
            try:
                merged = await rehydrator.merge_output(
                    org_id=instance.org_id,
                    instance_id=instance_id,
                    step_attempt_id=exe.id,
                    safe_output=output,
                    projector_version=exe.projector_version,
                )
                # R17 self-audit: `error` was rendered from `exe.error`, the
                # STORED value — the redaction marker under the flip, with
                # the raw vaulted under `RawTraceKind.ERROR`. `merge_error`
                # exists for exactly this and instance-detail calls it;
                # explain never did, so a grant holder got the marker beside
                # `raw_included: true`. Same class as the round-16 finding,
                # on a third field.
                merged_error = await rehydrator.merge_error(
                    org_id=instance.org_id,
                    instance_id=instance_id,
                    step_attempt_id=exe.id,
                    safe_error=exe.error,
                )
            except RawTraceUnavailable as exc:
                logger.warning("explain recovery failed: %s", exc)
                merged = output
                complete = False
            else:
                # R17 finding: this looked only at `merged`. `merge_error`
                # returns the stored MARKER when its vault row is absent —
                # deliberately, so the caller can report `partial` — so a
                # missing error record left `complete` True and the response
                # claimed a full release while `error` was still the marker.
                # A timeout was reported correctly because it raises; an
                # absent record does not.
                complete = not has_redaction_marker(merged) and not has_redaction_marker(
                    merged_error
                )
            audit_ok, reason = await commit_raw_release(
                repositories,
                request_id=request_id,
                surface=SURFACE_EXPLAIN,
                actor_id=user.sub,
                instance_id=instance_id,
                returned_kinds=exp_kinds if complete else (),
                withheld_kinds=() if complete else exp_kinds,
            )
            released = audit_ok and complete
            if released:
                raw_tcs = merged.get("tool_calls") or []
                # R16 finding: recovery SUCCEEDED and the result was then
                # discarded for everything except `tool_calls`. The main
                # fields were built from `output`, the stored projection, so
                # a grant holder saw `output: {"_withheld_keys": true}` and
                # `output_text: null` alongside `raw_included: true` and a
                # recorded `released` outcome. Retrieving raw and not using
                # it is the same defect as not retrieving it, with a worse
                # audit trail — the log says released and nothing was.
                presented = merged
        tool_calls = []
        for i, a in enumerate(audit_tcs):
            fallback = raw_tcs[i] if i < len(raw_tcs) and isinstance(raw_tcs[i], dict) else {}
            tool_calls.append(
                {
                    # R13 self-audit: the tool NAME comes from the audit
                    # detail, which `safe_tool_call` redacts — so a grant
                    # holder saw a redacted name even though the rehydrated
                    # tool call beside it has the real one. Same shape as
                    # finding 1: the release happened, the reader still got
                    # the projection.
                    "name": (fallback.get("name") or a.detail.get("name"))
                    if released
                    else a.detail.get("name"),
                    "input": _excerpt(a.detail.get("input", fallback.get("input")))
                    if released
                    else None,
                    "result": _excerpt(a.detail.get("result", fallback.get("result")))
                    if released
                    else None,
                    "timestamp": _iso(a.timestamp),
                    **({} if released else {"_redacted": "raw-trace grant only"}),
                }
            )
        common: dict[str, Any] = {
            "instance_id": instance_id,
            "step_id": step_id,
            "attempt": exe.attempt,
            "state": exe.state.value,
            "kind": kind,
            "started_at": _iso(exe.started_at),
            "completed_at": _iso(exe.completed_at),
            "error": redact_error(merged_error if released else exe.error, admin=released),
            "raw_included": released,
            **({"redaction_reason": reason} if reason is not None else {}),
        }
        if kind == "agentic":
            usage = presented.get("usage") or {}
            return {
                **common,
                "model": presented.get("model"),
                "memory_hash": presented.get("memory_hash"),
                "stop_reason": presented.get("stop_reason"),
                "iterations": usage.get("iterations"),
                "usage": usage,
                "cost_usd": presented.get("cost_usd"),
                "goal": _excerpt(getattr(step_def, "goal", None)),
                "system_prompt": _excerpt(getattr(step_def, "system_prompt", None)),
                # P2/§1.1: free-form model output is raw BY TAINT — whether or
                # not the step called a tool. The old `or not step_used_tool`
                # released it to any below-grant reader (re-review finding 2).
                "output_text": (
                    _excerpt(presented.get("output_text")) if released else _REDACTED_GRANT_ONLY
                ),
                "tool_calls": tool_calls,
            }
        return {
            **common,
            "function": getattr(step_def, "function", None),
            "config": _excerpt(getattr(step_def, "config", None)),
            # P2: a deterministic step's output is NOT automatically safe — it
            # carries whatever the function returned. Project it below grant.
            "output": _excerpt(redact_tool_data(presented, released, kind="step_output")),
        }

    return router


__all__ = ["AuditEntry", "StepExecution", "WorkflowInstance", "build_router"]
