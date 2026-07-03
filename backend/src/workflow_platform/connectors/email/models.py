"""Provider-agnostic email types.

These shapes are what crosses the boundary between the engine and any
email connector: triggers emit `EmailMessage` instances as their payload,
the agent's `email_send` tool builds `EmailSendRequest` instances. Concrete
connectors translate to/from provider-specific formats.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EmailProvider = Literal["gmail", "outlook", "imap"]


class EmailAddress(BaseModel):
    address: str
    name: str | None = None


class EmailAttachment(BaseModel):
    """Attachment metadata on an inbound message. The bytes are NOT fetched
    during a poll (each needs a separate per-attachment API call) — use the
    connector's `download_attachment(message_id, attachment_id)`."""

    filename: str
    mime_type: str
    attachment_id: str
    size_bytes: int = 0


class EmailMessage(BaseModel):
    """Inbound email shape — what triggers emit as their event payload."""

    provider: EmailProvider
    message_id: str
    thread_id: str | None = None
    from_address: EmailAddress
    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    subject: str
    body_text: str
    body_html: str | None = None
    received_at: datetime
    labels: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    attachments: list[EmailAttachment] = Field(default_factory=list)


class EmailSendRequest(BaseModel):
    """Outbound send shape — what the agent / send tool builds.

    Threading: `reply_to_message_id` is the single message-id being replied
    to. The connector is responsible for fetching that message's
    `References` header and appending the referenced id so the reply
    maintains the chain. Workflow YAMLs don't need to know about
    `References` — only the connector does.
    """

    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    subject: str
    body_text: str
    body_html: str | None = None
    reply_to_message_id: str | None = None
    labels_to_apply: list[str] = Field(default_factory=list)
