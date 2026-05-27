"""One-shot OAuth consent CLI for the Gmail connector — Gate 4 of the
operator setup in `docs/EMAIL_CONNECTOR_PLAN.md`.

Reads the OAuth client credentials from `.secrets/gmail/<account>/client_credentials.json`
(Gate 3 result), opens a browser to Google's consent screen, captures the
authorization code on a localhost callback, exchanges for access + refresh
tokens, and persists the refresh token to:

  1. `.secrets/gmail/<account>/refresh_token`  (chmod 0600, gitignored)
  2. `SecretStore` (process-local in `EnvSecretStore`; persistent in
     `AwsSecretsManagerStore` once the Terraform stack is applied)

The on-disk path is the source of truth in solo-dev mode. A future
"load secrets on startup" helper will seed `SecretStore` from there.

Usage:
    cd backend
    uv run python tools/gmail_auth.py --account <your@gmail.com>

Each gate has an explicit FAIL message so an operator hitting a snag can
map it back to the matching gate in `docs/EMAIL_CONNECTOR_PLAN.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow running directly without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from workflow_platform.secrets import (  # noqa: E402
    AwsSecretsManagerStore,
    EnvSecretStore,
    SecretStore,
)

# Full-mailbox scope, per `docs/EMAIL_CONNECTOR_PLAN.md` Gate 2.
SCOPES = ["https://mail.google.com/"]

# `.secrets/` lives at the project root (one above `backend/`).
SECRETS_ROOT = ROOT.parent / ".secrets" / "gmail"


def _fail(msg: str, *, gate: str | None = None) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    if gate:
        print(
            f"      See {gate} in docs/EMAIL_CONNECTOR_PLAN.md.",
            file=sys.stderr,
        )
    sys.exit(2)


def _client_credentials_path(account: str) -> Path:
    return SECRETS_ROOT / account / "client_credentials.json"


def _refresh_token_path(account: str) -> Path:
    return SECRETS_ROOT / account / "refresh_token"


def _load_client_config(account: str) -> dict[str, Any]:
    path = _client_credentials_path(account)
    if not path.exists():
        _fail(
            f"Client credentials missing at {path}. "
            f"Download the OAuth client JSON from GCP console and place it there.",
            gate="Gate 3",
        )
    try:
        config: dict[str, Any] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        _fail(f"Client credentials at {path} are not valid JSON: {exc}", gate="Gate 3")
        raise  # unreachable, satisfies mypy

    if "installed" not in config:
        _fail(
            f"Client credentials at {path} missing 'installed' section. "
            f"Expected Desktop-app OAuth JSON (Application type: Desktop app).",
            gate="Gate 3",
        )
    return config


def _build_secret_store() -> SecretStore:
    """Pick a store based on `WORKFLOW_PLATFORM_SECRET_BACKEND`. Default
    is `env` for solo-dev. Set to `aws` once Terraform is applied."""
    backend = os.environ.get("WORKFLOW_PLATFORM_SECRET_BACKEND", "env").lower()
    if backend == "aws":
        return AwsSecretsManagerStore()
    return EnvSecretStore()


async def _persist_refresh_token(account: str, refresh_token: str) -> None:
    # 1. Disk — source of truth in dev mode.
    path = _refresh_token_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refresh_token)
    path.chmod(0o600)
    print(f"  - refresh_token persisted to {path} (chmod 0600)")

    # 2. SecretStore — pre-loaded for live runs in the current process,
    #    persisted to AWS Secrets Manager when the backend is `aws`.
    store = _build_secret_store()
    key = f"gmail/{account}/refresh_token"
    await store.put(key, refresh_token)
    print(f"  - refresh_token seeded into {type(store).__name__} at key {key!r}")


def _run_consent_flow(client_config: dict[str, Any], account: str) -> Any:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        _fail(
            f"google-auth-oauthlib not installed: {exc}. Run `uv sync` from the backend/ directory."
        )

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # `port=0` picks a free localhost port. The browser opens automatically.
    # `prompt='select_account'` forces Google to show the account picker — without
    # it, an already-signed-in browser session is auto-used and consent silently
    # binds to the wrong account. `login_hint` pre-suggests the right one in the
    # picker so the user just clicks "Continue."
    return flow.run_local_server(
        port=0,
        prompt="select_account",
        login_hint=account,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Gmail OAuth consent flow for one account and persist "
            "the resulting refresh token."
        )
    )
    parser.add_argument(
        "--account", required=True, help="The Gmail address (e.g. you@example.com)."
    )
    args = parser.parse_args()
    account = args.account.strip()

    print(f"Gmail OAuth consent for account: {account}")
    print(f"  scopes: {SCOPES}")
    print()

    client_config = _load_client_config(account)

    print("Opening browser for consent. Sign in as the project account and accept the scopes.")
    credentials = _run_consent_flow(client_config, account)
    refresh_token = credentials.refresh_token

    if not refresh_token:
        _fail(
            "Google returned no refresh token. This usually means the account already "
            "consented to this OAuth client; Google then re-uses the existing refresh "
            "token instead of issuing a new one. Revoke at "
            "https://myaccount.google.com/permissions and re-run.",
            gate="Gate 4",
        )

    print()
    print("Consent granted. Persisting refresh token:")
    asyncio.run(_persist_refresh_token(account, refresh_token))

    print()
    print(f"OK — Gate 4 complete for {account}.")
    print(
        f"     Live workflows can now construct a `GmailOAuthProvider(account={account!r}, ...)` "
        f"and the Gmail connector will use it."
    )


if __name__ == "__main__":
    main()
