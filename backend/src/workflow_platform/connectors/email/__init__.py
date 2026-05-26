"""Email connector subpackage.

Provider-agnostic ABC + Pydantic models in this layer; provider-specific
concrete connectors (Gmail, Outlook, IMAP) live alongside in their own
modules. See `docs/EMAIL_CONNECTOR_PLAN.md` for the design.
"""

from workflow_platform.connectors.email.base import EmailConnector
from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
from workflow_platform.connectors.email.gmail import (
    GmailAuthProvider,
    GmailConnector,
    GmailLabelNotFound,
    GmailMessageNotFound,
)
from workflow_platform.connectors.email.gmail_auth import (
    GmailAuthError,
    GmailAuthMisconfigured,
    GmailAuthRevoked,
    GmailOAuthProvider,
)
from workflow_platform.connectors.email.models import (
    EmailAddress,
    EmailMessage,
    EmailProvider,
    EmailSendRequest,
)

__all__ = [
    "EmailAddress",
    "EmailConnector",
    "EmailMessage",
    "EmailProvider",
    "EmailSendRequest",
    "GmailAuthError",
    "GmailAuthMisconfigured",
    "GmailAuthProvider",
    "GmailAuthRevoked",
    "GmailConnector",
    "GmailLabelNotFound",
    "GmailMessageNotFound",
    "GmailOAuthProvider",
    "maybe_build_gmail_connector",
]
