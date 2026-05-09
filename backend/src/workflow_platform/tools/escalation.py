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
from workflow_platform.persistence import AuditEntry, AuditRepo
from workflow_platform.persistence.models import _new_id, _utcnow
from workflow_platform.tools.base import Tool, ToolContext, ToolResult


class RequestHumanReviewTool(Tool):
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

    def __init__(self, audit_repo: AuditRepo, events: EventBus | None = None) -> None:
        self.audit_repo = audit_repo
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

        entry = AuditEntry(
            id=_new_id(),
            timestamp=_utcnow(),
            actor_type="agent",
            actor_id=(context.agent_id if context and context.agent_id else "agent"),
            action="escalation_requested",
            workflow_instance_id=(context.workflow_instance_id if context else None),
            detail={"reason": reason, "context": extra},
        )
        await self.audit_repo.append(entry)
        if self.events is not None:
            await self.events.publish(entry.model_dump(mode="json"))
        return ToolResult(
            content={"escalation_id": entry.id, "status": "pending", "reason": reason}
        )
