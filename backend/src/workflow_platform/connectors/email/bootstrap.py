"""Best-effort sync construction of a `GmailConnector` for process-startup
wiring (`main.py`, `tools/fire.py`).

Solves the chicken-and-egg between sync engine construction and async
SecretStore access: at process start, we want to decide whether to add
`EmailSendTool` / `EmailLabelApplyTool` to the engine's catalog. That
decision depends on whether credentials are reachable — but `create_app`
isn't async, so we can't `await store.get(...)`.

Strategy: special-case `EnvSecretStore` (the dev path) by reading
`.secrets/gmail/<account>/` directly and writing into `os.environ` —
which is exactly what `EnvSecretStore.get` reads. For other stores
(`AwsSecretsManagerStore` in prod), we assume credentials are already
populated and build the connector optimistically; real availability
shows up at the first runtime call.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from workflow_platform.connectors.email.gmail import GmailConnector
from workflow_platform.connectors.email.gmail_auth import GmailOAuthProvider
from workflow_platform.secrets import EnvSecretStore, SecretStore

logger = logging.getLogger(__name__)

# `.secrets/` lives at the project root, five `parents` up from this file:
# backend/src/workflow_platform/connectors/email/bootstrap.py.
_SECRETS_ROOT = Path(__file__).resolve().parents[5] / ".secrets" / "gmail"


def maybe_build_gmail_connector(
    *,
    account: str | None,
    secret_store: SecretStore,
) -> GmailConnector | None:
    """Build a `GmailConnector` if credentials are available; else None.

    Returns None when:
      - `account` is None or empty (no wiring requested).
      - `secret_store` is an `EnvSecretStore` *and* neither the env nor
        `.secrets/gmail/<account>/` has the required credentials.

    For non-`EnvSecretStore` stores (notably `AwsSecretsManagerStore`),
    the function assumes credentials are pre-populated and builds the
    connector unconditionally. If they aren't actually there, the first
    `GmailOAuthProvider.access_token()` call raises
    `GmailAuthMisconfigured` — surfaced via the agent tool's error path.
    """
    if not account:
        return None
    if isinstance(secret_store, EnvSecretStore) and not seed_gmail_env_from_disk(account):
        logger.warning(
            "Gmail account %r set but credentials not found in env or %s. "
            "Email tools will NOT be wired into the engine catalog. "
            "Complete Gates 3+4 in docs/EMAIL_CONNECTOR_PLAN.md.",
            account,
            _SECRETS_ROOT / account,
        )
        return None

    provider = GmailOAuthProvider(account=account, secret_store=secret_store)
    return GmailConnector(account=account, auth_provider=provider)


def seed_gmail_env_from_disk(account: str) -> bool:
    """If `.secrets/gmail/<account>/` has both credential files and the
    process env doesn't already, populate `os.environ` so
    `EnvSecretStore.get(...)` succeeds.

    Returns True if credentials are available (either already in env or
    just seeded). False if neither source has them.
    """
    creds_key = f"gmail/{account}/client_credentials"
    token_key = f"gmail/{account}/refresh_token"
    if creds_key in os.environ and token_key in os.environ:
        return True
    creds_path = _SECRETS_ROOT / account / "client_credentials.json"
    token_path = _SECRETS_ROOT / account / "refresh_token"
    if not (creds_path.exists() and token_path.exists()):
        return False
    os.environ[creds_key] = creds_path.read_text()
    os.environ[token_key] = token_path.read_text().strip()
    return True
