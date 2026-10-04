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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workflow_platform.connectors.email.gmail import GmailConnector
from workflow_platform.connectors.email.gmail_auth import GmailOAuthProvider
from workflow_platform.secrets import EnvSecretStore, SecretStore

logger = logging.getLogger(__name__)

# `.secrets/` lives at the project root, five `parents` up from this file:
# backend/src/workflow_platform/connectors/email/bootstrap.py.
_SECRETS_ROOT = Path(__file__).resolve().parents[5] / ".secrets" / "gmail"

# Consumer Google accounts can only consent through an EXTERNAL OAuth
# consent screen, and ours is in Testing status (EMAIL_CONNECTOR_PLAN
# Gate 2: production verification for the full-mailbox scope is not worth
# it), where Google kills refresh tokens seven days after consent. That
# clock stopped the classifier four times between 2026-07-26 and
# 2026-09-25, each outage landing ~7 days after a re-consent. Workspace
# accounts can use an Internal screen with no clock, so they are not
# listed — a wrong "no clock" costs a missed warning that the auth-revoked
# alert still catches; a wrong "clock" would cry wolf weekly.
CONSENT_TTL_BY_DOMAIN: dict[str, timedelta] = {
    "gmail.com": timedelta(days=7),
    "googlemail.com": timedelta(days=7),
}


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


def reseed_gmail_env_from_disk(account: str) -> bool:
    """Re-read `.secrets/gmail/<account>/` into `os.environ` even when the
    env already holds credentials, and report whether the refresh token
    CHANGED.

    `seed_gmail_env_from_disk` seeds once and then defers to the env, which
    is right at startup and wrong after a re-consent: the provider re-reads
    the store on every refresh, but the store is this process's env, so a
    fresh token written by `gmail_auth.py` was invisible until a restart —
    while the trigger's log line told the operator re-consenting was enough
    (2026-09-28). Called from the trigger's revoked back-off, so recovery
    needs the consent CLI and nothing else.
    """
    creds_path = _SECRETS_ROOT / account / "client_credentials.json"
    token_path = _SECRETS_ROOT / account / "refresh_token"
    if not (creds_path.exists() and token_path.exists()):
        return False
    token_key = f"gmail/{account}/refresh_token"
    token = token_path.read_text().strip()
    changed = os.environ.get(token_key) != token
    os.environ[f"gmail/{account}/client_credentials"] = creds_path.read_text()
    os.environ[token_key] = token
    return changed


def consent_expires_at(account: str) -> datetime | None:
    """When this account's refresh token will be killed by the Testing-status
    clock, or None when the account has no clock (see
    `CONSENT_TTL_BY_DOMAIN`) or no token on disk.

    Consent time is the refresh-token file's mtime: `gmail_auth.py` writes it
    exactly once per consent, and nothing else touches it.
    """
    ttl = CONSENT_TTL_BY_DOMAIN.get(account.rpartition("@")[2].lower())
    if ttl is None:
        return None
    token_path = _SECRETS_ROOT / account / "refresh_token"
    try:
        consented = datetime.fromtimestamp(token_path.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    return consented + ttl


def credentialed_accounts() -> list[str]:
    """Accounts with complete credentials under `.secrets/gmail/<account>/`.
    Dev/EnvSecretStore surface — the same on-disk layout the per-trigger
    seeding uses."""
    if not _SECRETS_ROOT.is_dir():
        return []
    return sorted(
        d.name
        for d in _SECRETS_ROOT.iterdir()
        if d.is_dir()
        and (d / "client_credentials.json").exists()
        and (d / "refresh_token").exists()
    )
