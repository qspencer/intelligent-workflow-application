"""EmailConnector — provider-agnostic email ABC.

Sits between `Connector` (the generic connector interface) and concrete
provider implementations (`GmailConnector`, `OutlookConnector`,
`ImapSmtpConnector`). Subclasses implement the four typed abstract
methods; the inherited `Connector` methods (`trigger_poll`, `send`,
`health_check`) get default impls here that adapt typed↔dict so the
generic engine plumbing keeps working without a special case for email.

Cursor semantics: the default `trigger_poll` keeps a `_cursor` of the
most-recently-seen `received_at` and asks the provider for messages
strictly newer than that. Subclasses can override `trigger_poll` if
their provider exposes a better cursor (Gmail's `historyId`, Microsoft
Graph's delta-link).
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from workflow_platform.connectors.base import Connector
from workflow_platform.connectors.email.models import EmailMessage, EmailSendRequest


class EmailConnector(Connector):
    type: ClassVar[str]

    def __init__(self) -> None:
        self._cursor: datetime | None = None

    # --- abstract: subclasses must implement ---

    @abstractmethod
    async def poll_inbox(
        self,
        since: datetime | None = None,
        label: str | None = None,
        max_messages: int = 50,
        query: str | None = None,
    ) -> list[EmailMessage]:
        """Return messages received strictly after `since`, newest-first
        order optional. Returns at most `max_messages` entries. `query` is an
        extra provider-native search clause (providers without server-side
        search may ignore it)."""

    @abstractmethod
    async def send_email(self, req: EmailSendRequest) -> str:
        """Send a message. Returns the provider's message_id."""

    async def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Fetch one attachment's bytes. Default: unsupported — providers
        implement it when their API exposes attachment content (Gmail does).
        Matches the connector convention of NotImplementedError defaults so
        providers only implement what they support."""
        raise NotImplementedError(f"{type(self).__name__} does not support attachment download")

    @abstractmethod
    async def apply_labels(self, message_id: str, labels: list[str]) -> None:
        """Apply labels (or the provider's closest non-destructive equivalent
        — categories on Outlook, flags on IMAP). See
        `docs/EMAIL_CONNECTOR_PLAN.md` 'Cross-provider label semantics'."""

    @abstractmethod
    async def authenticate(self) -> None:
        """Establish or refresh credentials. Idempotent."""

    # --- concrete defaults: subclasses can override for provider-native cursors ---

    async def health_check(self) -> bool:
        try:
            await self.poll_inbox(max_messages=1)
            return True
        except Exception:
            return False

    async def trigger_poll(self) -> list[dict[str, Any]]:
        messages = await self.poll_inbox(since=self._cursor)
        if messages:
            self._cursor = max(m.received_at for m in messages)
        return [m.model_dump(mode="json") for m in messages]

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = EmailSendRequest.model_validate(payload)
        message_id = await self.send_email(req)
        return {"message_id": message_id}
