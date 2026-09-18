"""`request_human_review` — let an agent escalate to a human operator.

The tool writes an `escalation_requested` audit entry; the dashboard's
escalation queue lists unresolved entries (paired with `escalation_resolved`
entries the operator emits via the API).

Per `docs/ARCHITECTURE.md` D7, the escalation chain is:
    Step Agent → Workflow Agent → Orchestrator → Human Operator
For Phase 2 / Week 9 only the human-operator hop is implemented; the
intermediate hops (workflow agent, orchestrator) are LLM-driven and arrive
when the orchestrator gets its active-reasoning brain.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.events import EventBus
from workflow_platform.persistence import AuditEntry, AuditRepo, Repositories
from workflow_platform.persistence.models import _new_id, _utcnow
from workflow_platform.tools.base import Tool, ToolContext, ToolResult
from workflow_platform.trace_flip import trace_safe_only_from_env
from workflow_platform.trace_projection import (
    PROJECTOR_VERSION,
    project_audit_detail_at_rest,
)
from workflow_platform.trace_vault import RawTraceVault, audit_detail_has_raw


def _trace_safe_only() -> bool:
    """The safe-only flip. The escalation tool writes audit outside the
    engine's `_audit` chokepoint, so it consults the flag itself — but through
    the ONE reader, not its own copy of the spelling."""
    return trace_safe_only_from_env()


class RequestHumanReviewTool(Tool):
    effect = "mutating"
    name: ClassVar[str] = "request_human_review"
    description: ClassVar[str] = (
        "Escalate to a human operator. Call this when stuck, uncertain, or out "
        "of options. Provide a clear `reason` and any helpful `context`. The "
        "human reviews via the dashboard and resolves the escalation."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why human review is needed."},
            "context": {
                "type": "object",
                "description": "Free-form context that will help the reviewer (state, tried options, etc.).",
            },
        },
        "required": ["reason"],
    }

    def __init__(
        self,
        audit_repo: AuditRepo,
        events: EventBus | None = None,
        repositories: Repositories | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        # R12 finding 4: this tool projected its detail at rest but never
        # VAULTED it, so `reason` and `context` — the model-authored content
        # the escalation exists to convey — were DESTROYED rather than
        # withheld. With repositories it vaults first, like the engine
        # chokepoint. Without them (older call sites, unit tests) it keeps the
        # previous behaviour, and `_vault is None` is the only case where
        # projection is allowed to be lossy.
        self._repos = repositories
        self._vault = RawTraceVault(repositories) if repositories is not None else None
        self.events = events

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        reason = params.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return ToolResult(error="reason is required")
        extra = params.get("context") or {}
        if not isinstance(extra, dict):
            return ToolResult(error="context must be an object")

        # F4 (G-Trace-Review-4): reason + context are MODEL-AUTHORED raw. Under
        # the flip they must not land at rest — this tool writes audit directly
        # (not through the engine `_audit` chokepoint), so it applies the same
        # shared action-aware projection itself. The flip is a process-wide env
        # flag, so the tool can read it. (Below-grant READS are gated separately
        # in /api/escalations; this closes the AT-REST write path.)
        raw_detail: dict[str, Any] = {"reason": reason, "context": extra}
        detail = raw_detail
        entry_id = _new_id()
        instance_id = context.workflow_instance_id if context else None
        stamp: str | None = None
        if _trace_safe_only():
            detail = project_audit_detail_at_rest("escalation_requested", raw_detail)
            lossy = audit_detail_has_raw("escalation_requested", raw_detail)
            # R13 finding 3: vaulting used to be BEST-EFFORT — constructed
            # without `repositories`, or called without an instance, the tool
            # wrote the projected entry and no vault row, silently destroying
            # the model-authored reason/context this escalation exists to
            # carry. Optional preservation of something projection will
            # remove is not preservation. Refuse instead.
            if lossy and (self._vault is None or self._repos is None):
                return ToolResult(
                    error="cannot record escalation: safe-only mode would remove the "
                    "reason/context, and this tool was constructed without the "
                    "repositories needed to preserve them in the vault"
                )
            if lossy and instance_id is None:
                return ToolResult(
                    error="cannot record escalation: safe-only mode would remove the "
                    "reason/context, and there is no workflow instance to vault them "
                    "against (the vault is instance-scoped)"
                )
            # Vault BEFORE projecting away, addressed by the entry id — the
            # same order and the same key space as the engine chokepoint.
            if (
                self._vault is not None
                and self._repos is not None
                and instance_id is not None
                and lossy
            ):
                instance = await self._repos.instances.get(instance_id)
                if instance is None:
                    # Fail closed. Guessing the default org would file one
                    # tenant's raw under another; projecting anyway would
                    # destroy it. Neither is acceptable, so refuse.
                    return ToolResult(
                        error="cannot record escalation: the owning org of "
                        f"instance {instance_id} could not be resolved"
                    )
                await self._vault.record_audit_detail(
                    org_id=instance.org_id,
                    instance_id=instance_id,
                    audit_entry_id=entry_id,
                    action="escalation_requested",
                    detail=raw_detail,
                )
                stamp = PROJECTOR_VERSION
        entry = AuditEntry(
            id=entry_id,
            timestamp=_utcnow(),
            actor_type="agent",
            actor_id=(context.agent_id if context and context.agent_id else "agent"),
            action="escalation_requested",
            workflow_instance_id=instance_id,
            detail=detail,
            projector_version=stamp,
        )
        await self.audit_repo.append(entry)
        if self.events is not None:
            await self.events.publish(entry.model_dump(mode="json"))
        return ToolResult(
            content={"escalation_id": entry.id, "status": "pending", "reason": reason}
        )
