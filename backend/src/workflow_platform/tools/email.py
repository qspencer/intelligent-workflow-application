"""Agent-callable Gmail tools.

Two tools, both bound to a single `GmailConnector` instance at construction:

- `EmailSendTool` (name `email_send`) — send a reply / new message. The
  connector handles RFC 5322 building + threading-header construction;
  this tool just validates the params via the `EmailSendRequest` shape
  and dispatches.
- `EmailLabelApplyTool` (name `email_label_apply`) — apply one or more
  Gmail labels to a message id.

Capability gating happens at the `Agent` layer via `tool_allowed(name)`;
neither tool checks capabilities internally. To restrict a workflow's
agent to "no email sending," set `capabilities.tools` to a list that
omits `email_send` / `email_label_apply` — the agent's dispatch returns
a `Capability denied` `ToolResult` before this code runs.

v1 wires each tool to exactly one Gmail account. Multi-account support
would either thread an `account` parameter through every tool call or
construct one tool instance per account; the trigger that pulls multi-
account into the platform decides.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import ValidationError

from workflow_platform.connectors.email.gmail import (
    GmailConnector,
    GmailLabelNotFound,
    GmailMessageNotFound,
)
from workflow_platform.connectors.email.gmail_auth import GmailAuthRevoked
from workflow_platform.connectors.email.models import EmailSendRequest
from workflow_platform.tools.base import Tool, ToolContext, ToolResult

_ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "address": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["address"],
}


class EmailSendTool(Tool):
    name: ClassVar[str] = "email_send"
    description: ClassVar[str] = (
        "Send an email via the bound Gmail account. Provide at minimum `to`, "
        "`subject`, and `body_text`. To reply to an existing message and "
        "maintain threading, pass `reply_to_message_id` — the connector "
        "fetches the prior message and constructs In-Reply-To + References "
        "headers automatically. Optional `labels_to_apply` are applied to "
        "the sent message after delivery."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": _ADDRESS_SCHEMA, "minItems": 1},
            "cc": {"type": "array", "items": _ADDRESS_SCHEMA},
            "bcc": {"type": "array", "items": _ADDRESS_SCHEMA},
            "subject": {"type": "string"},
            "body_text": {"type": "string"},
            "body_html": {"type": "string"},
            "reply_to_message_id": {"type": "string"},
            "labels_to_apply": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["to", "subject", "body_text"],
    }

    def __init__(self, connector: GmailConnector) -> None:
        self.connector = connector

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        try:
            request = EmailSendRequest.model_validate(params)
        except ValidationError as exc:
            return ToolResult(error=f"Invalid email_send params: {exc.errors()}")
        try:
            message_id = await self.connector.send_email(request)
        except GmailAuthRevoked as exc:
            return ToolResult(error=f"Gmail auth revoked — operator must re-run consent: {exc}")
        except Exception as exc:
            return ToolResult(error=f"Email send failed: {exc}")
        return ToolResult(content={"message_id": message_id})


def account_label_tool_name(account: str) -> str:
    """Bedrock-legal per-account tool name: `toolSpec.name` must match
    [a-zA-Z0-9_-]+ (found live — a colon/@/dot name fails the Converse
    call with a ValidationException). Deterministic and collision-safe for
    real addresses: every illegal char becomes `_`."""
    return "email_label_apply__" + re.sub(r"[^a-zA-Z0-9_-]", "_", account)


class EmailLabelApplyTool(Tool):
    name: ClassVar[str] = "email_label_apply"
    description: ClassVar[str] = (
        "Apply one or more Gmail labels to a message id. Label names are "
        "resolved to label IDs via the account's label list; system labels "
        "(INBOX, UNREAD, etc.) work directly. Use this to mark a triage "
        "outcome on the original message (e.g. 'triaged/urgent')."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "message_id": {"type": "string"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["message_id", "labels"],
    }

    def __init__(
        self,
        connector: GmailConnector,
        *,
        name: str | None = None,
        allowed_labels: list[str] | None = None,
    ) -> None:
        self.connector = connector
        # Per-account catalog registration (EMAIL_TRIAGE_ACT_PLAN §4): a
        # non-default name like "email_label_apply:<account>" makes the
        # capability allowlist say WHICH mailbox is writable. Instance attr
        # shadows the ClassVar; to_bedrock_tool_spec and the catalog both
        # read `self.name`, so this composes with everything downstream.
        if name is not None:
            self.name = name  # type: ignore[misc]
        # First fence of two: requests outside this list fail before any API
        # call (Gmail's refuse-to-create resolution is the second).
        self.allowed_labels = set(allowed_labels) if allowed_labels is not None else None

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        message_id = params.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return ToolResult(error="message_id is required (non-empty string)")
        raw_labels = params.get("labels")
        if not isinstance(raw_labels, list) or not raw_labels:
            return ToolResult(error="labels must be a non-empty list of strings")
        if not all(isinstance(lbl, str) and lbl for lbl in raw_labels):
            return ToolResult(error="every label must be a non-empty string")
        labels: list[str] = list(raw_labels)
        if self.allowed_labels is not None:
            refused = sorted(set(labels) - self.allowed_labels)
            if refused:
                return ToolResult(error=f"Labels not in this tool's allowlist: {refused}")
        try:
            await self.connector.apply_labels(message_id, labels)
        except GmailLabelNotFound as exc:
            return ToolResult(error=str(exc))
        except GmailMessageNotFound as exc:
            return ToolResult(error=f"Gmail message not found: {exc}")
        except GmailAuthRevoked as exc:
            return ToolResult(error=f"Gmail auth revoked — operator must re-run consent: {exc}")
        except Exception as exc:
            return ToolResult(error=f"Label apply failed: {exc}")
        return ToolResult(content={"message_id": message_id, "labels_applied": labels})
