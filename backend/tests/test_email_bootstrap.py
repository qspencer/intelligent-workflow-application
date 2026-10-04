"""Tests for `workflow_platform.connectors.email.bootstrap`.

The helper is what `main.py` + `tools/fire.py` use at process startup to
decide whether to wire `EmailSendTool` + `EmailLabelApplyTool` into the
engine's catalog. Coverage:

  - returns None when no account is configured
  - returns None when account is set but no credentials anywhere
  - builds a connector when env already has the secrets
  - builds a connector by seeding env from `.secrets/gmail/<account>/`
  - for non-EnvSecretStore (AwsSecretsManagerStore stand-in), builds
    optimistically without checking
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from workflow_platform.connectors.email import (
    GmailConnector,
    maybe_build_gmail_connector,
)
from workflow_platform.connectors.email.bootstrap import seed_gmail_env_from_disk
from workflow_platform.secrets import EnvSecretStore, SecretStore

ACCOUNT = "test-bootstrap@example.com"
CREDS_KEY = f"gmail/{ACCOUNT}/client_credentials"
TOKEN_KEY = f"gmail/{ACCOUNT}/refresh_token"

CLIENT_CREDS_JSON = json.dumps(
    {
        "installed": {
            "client_id": "x.apps.googleusercontent.com",
            "client_secret": "secret-x",
            "project_id": "test-project",
        }
    }
)


@pytest.fixture(autouse=True)
def _cleanup_env() -> Iterator[None]:
    """Strip any leaked Gmail env vars before + after each test."""
    for key in (CREDS_KEY, TOKEN_KEY):
        os.environ.pop(key, None)
    yield
    for key in (CREDS_KEY, TOKEN_KEY):
        os.environ.pop(key, None)


# ---------- account gating ----------


def test_returns_none_when_account_is_none() -> None:
    assert maybe_build_gmail_connector(account=None, secret_store=EnvSecretStore()) is None


def test_returns_none_when_account_is_empty_string() -> None:
    assert maybe_build_gmail_connector(account="", secret_store=EnvSecretStore()) is None


# ---------- EnvSecretStore path ----------


def test_returns_none_when_env_and_disk_both_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No env vars set + .secrets/ doesn't have the files = no connector."""
    with (
        patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path),
        caplog.at_level("WARNING"),
    ):
        result = maybe_build_gmail_connector(account=ACCOUNT, secret_store=EnvSecretStore())
    assert result is None
    assert any("credentials not found" in rec.message.lower() for rec in caplog.records)


def test_builds_connector_when_env_already_has_secrets() -> None:
    os.environ[CREDS_KEY] = CLIENT_CREDS_JSON
    os.environ[TOKEN_KEY] = "refresh-token-from-env"
    connector = maybe_build_gmail_connector(account=ACCOUNT, secret_store=EnvSecretStore())
    assert isinstance(connector, GmailConnector)
    assert connector.account == ACCOUNT


def test_builds_connector_by_seeding_env_from_disk(tmp_path: Path) -> None:
    """If env is empty but `.secrets/gmail/<account>/` has both files,
    the helper reads them into env and builds the connector."""
    account_dir = tmp_path / ACCOUNT
    account_dir.mkdir(parents=True)
    (account_dir / "client_credentials.json").write_text(CLIENT_CREDS_JSON)
    (account_dir / "refresh_token").write_text("refresh-token-from-disk\n")

    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        connector = maybe_build_gmail_connector(account=ACCOUNT, secret_store=EnvSecretStore())

    assert isinstance(connector, GmailConnector)
    # Env should now carry the seeded values (sans trailing newline on the token).
    assert os.environ[CREDS_KEY] == CLIENT_CREDS_JSON
    assert os.environ[TOKEN_KEY] == "refresh-token-from-disk"


def testseed_gmail_env_from_disk_is_idempotent(tmp_path: Path) -> None:
    """If env is already populated, seed_gmail_env_from_disk reports True without
    re-reading the disk (and without overwriting env)."""
    os.environ[CREDS_KEY] = "pre-existing-creds"
    os.environ[TOKEN_KEY] = "pre-existing-token"
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        # Disk files don't even exist, but env hits short-circuit.
        assert seed_gmail_env_from_disk(ACCOUNT) is True
    assert os.environ[CREDS_KEY] == "pre-existing-creds"
    assert os.environ[TOKEN_KEY] == "pre-existing-token"


def testseed_gmail_env_from_disk_returns_false_when_only_one_file_present(
    tmp_path: Path,
) -> None:
    """Both files must exist — having just one isn't enough."""
    (tmp_path / ACCOUNT).mkdir(parents=True)
    (tmp_path / ACCOUNT / "client_credentials.json").write_text(CLIENT_CREDS_JSON)
    # No refresh_token file.
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        assert seed_gmail_env_from_disk(ACCOUNT) is False
    # And env was not partially populated.
    assert CREDS_KEY not in os.environ
    assert TOKEN_KEY not in os.environ


# ---------- non-EnvSecretStore path ----------


class _FakeAwsStore(SecretStore):
    """Stand-in for AwsSecretsManagerStore — anything that isn't
    EnvSecretStore takes the optimistic path."""

    async def get(self, key: str) -> str:
        return "x"

    async def put(self, key: str, value: str) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass


def test_builds_optimistically_for_non_env_store(tmp_path: Path) -> None:
    """AwsSecretsManagerStore (or any non-env store) is assumed populated.
    The helper builds the connector without checking — real availability
    is a runtime concern."""
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        # Disk and env both empty — but for non-EnvSecretStore the helper
        # doesn't even look.
        connector = maybe_build_gmail_connector(account=ACCOUNT, secret_store=_FakeAwsStore())
    assert isinstance(connector, GmailConnector)
    assert connector.account == ACCOUNT


# ---------- main.py + fire.py integration ----------


def test_main_default_engine_includes_email_tools_when_account_configured(
    tmp_path: Path,
) -> None:
    """Smoke-check that main.py's `_build_default_tools` actually wires
    EmailSendTool + EmailLabelApplyTool into the catalog when the env is set."""
    from workflow_platform.main import _build_default_tools

    account_dir = tmp_path / ACCOUNT
    account_dir.mkdir(parents=True)
    (account_dir / "client_credentials.json").write_text(CLIENT_CREDS_JSON)
    (account_dir / "refresh_token").write_text("refresh-token-from-disk")

    with (
        patch.dict(os.environ, {"WORKFLOW_PLATFORM_GMAIL_ACCOUNT": ACCOUNT}, clear=False),
        patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path),
    ):
        tools = _build_default_tools(EnvSecretStore())

    tool_names = {t.name for t in tools}
    assert "email_send" in tool_names
    assert "email_label_apply" in tool_names
    # The always-on tools are still there.
    assert {"pdf_extract", "file_read", "file_write"}.issubset(tool_names)


def test_main_default_engine_excludes_email_tools_when_account_unset() -> None:
    """No `WORKFLOW_PLATFORM_GMAIL_ACCOUNT` = no email tools in the catalog.
    Default behavior preserved for dev installs without Gmail config."""
    from workflow_platform.main import _build_default_tools

    # Make sure the env var isn't set by mistake from an earlier test.
    env_minus_gmail: dict[str, Any] = {
        k: v for k, v in os.environ.items() if k != "WORKFLOW_PLATFORM_GMAIL_ACCOUNT"
    }
    with patch.dict(os.environ, env_minus_gmail, clear=True):
        tools = _build_default_tools(EnvSecretStore())

    tool_names = {t.name for t in tools}
    assert "email_send" not in tool_names
    assert "email_label_apply" not in tool_names
    assert {"pdf_extract", "file_read", "file_write"}.issubset(tool_names)


# ---------- re-consent without a restart + the consent clock (review R1) ----------


def _write_account(root: Path, account: str, token: str) -> Path:
    d = root / account
    d.mkdir(parents=True, exist_ok=True)
    (d / "client_credentials.json").write_text(CLIENT_CREDS_JSON)
    (d / "refresh_token").write_text(token + "\n")
    return d


def test_reseed_picks_up_a_new_token_that_seed_would_ignore(tmp_path: Path) -> None:
    """The 2026-09-28 finding: after a re-consent the fresh token sat on disk
    while the process kept the dead one in env, because seeding defers to an
    env that is already populated. Re-seeding must overwrite, and must say
    whether anything changed."""
    from workflow_platform.connectors.email.bootstrap import reseed_gmail_env_from_disk

    os.environ[CREDS_KEY] = CLIENT_CREDS_JSON
    os.environ[TOKEN_KEY] = "dead-token"
    _write_account(tmp_path, ACCOUNT, "fresh-token")
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        assert seed_gmail_env_from_disk(ACCOUNT) is True
        assert os.environ[TOKEN_KEY] == "dead-token"  # seed defers — the bug
        assert reseed_gmail_env_from_disk(ACCOUNT) is True
        assert os.environ[TOKEN_KEY] == "fresh-token"
        assert reseed_gmail_env_from_disk(ACCOUNT) is False  # nothing new


def test_reseed_without_files_changes_nothing(tmp_path: Path) -> None:
    from workflow_platform.connectors.email.bootstrap import reseed_gmail_env_from_disk

    os.environ[TOKEN_KEY] = "kept"
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        assert reseed_gmail_env_from_disk(ACCOUNT) is False
    assert os.environ[TOKEN_KEY] == "kept"


def test_consent_expiry_is_seven_days_after_consent_for_consumer_accounts(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from workflow_platform.connectors.email.bootstrap import consent_expires_at

    consumer = "someone@gmail.com"
    d = _write_account(tmp_path, consumer, "t")
    consented = datetime(2026, 9, 28, 19, 20, tzinfo=UTC)
    os.utime(d / "refresh_token", (consented.timestamp(), consented.timestamp()))
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        assert consent_expires_at(consumer) == consented + timedelta(days=7)
        # A clocked domain with no token on disk has nothing to expire.
        assert consent_expires_at("nobody@gmail.com") is None


def test_workspace_and_unknown_accounts_have_no_clock(tmp_path: Path) -> None:
    """A wrong "no clock" costs a missed warning the revoked alert still
    catches; a wrong "clock" would cry wolf every week."""
    from workflow_platform.connectors.email.bootstrap import consent_expires_at

    _write_account(tmp_path, ACCOUNT, "t")
    with patch("workflow_platform.connectors.email.bootstrap._SECRETS_ROOT", tmp_path):
        assert consent_expires_at(ACCOUNT) is None
