"""Tests for SecretStore implementations."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from workflow_platform.secrets import (
    AwsSecretsManagerStore,
    EnvSecretStore,
    SecretNotFoundError,
)

# --- EnvSecretStore ---


async def test_env_secret_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    store = EnvSecretStore()
    with pytest.raises(SecretNotFoundError):
        await store.get("MY_SECRET")
    await store.put("MY_SECRET", "shhh")
    assert await store.get("MY_SECRET") == "shhh"
    await store.delete("MY_SECRET")
    with pytest.raises(SecretNotFoundError):
        await store.get("MY_SECRET")


async def test_env_secret_store_get_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_HEADERS", '{"X-Token": "abc", "X-Source": "wp"}')
    store = EnvSecretStore()
    assert await store.get_json("WEBHOOK_HEADERS") == {
        "X-Token": "abc",
        "X-Source": "wp",
    }


async def test_env_secret_store_get_json_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAD", "not-json")
    store = EnvSecretStore()
    with pytest.raises(ValueError, match="not valid JSON"):
        await store.get_json("BAD")


# --- AwsSecretsManagerStore ---


async def test_aws_secret_store_get_returns_secret_string() -> None:
    store = AwsSecretsManagerStore()
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": "value-1"}
    store._client = fake_client
    assert await store.get("my-secret") == "value-1"
    fake_client.get_secret_value.assert_called_once_with(SecretId="my-secret")


async def test_aws_secret_store_get_raises_for_missing() -> None:
    store = AwsSecretsManagerStore()
    fake_client = MagicMock()
    err: Exception = type("ResourceNotFoundException", (Exception,), {})()
    fake_client.get_secret_value.side_effect = err
    store._client = fake_client
    with pytest.raises(SecretNotFoundError):
        await store.get("nope")


async def test_aws_secret_store_put_creates_when_missing() -> None:
    store = AwsSecretsManagerStore()
    fake_client = MagicMock()
    err: Exception = type("ResourceNotFoundException", (Exception,), {})()
    fake_client.put_secret_value.side_effect = err
    store._client = fake_client
    await store.put("new-secret", "v1")
    fake_client.put_secret_value.assert_called_once()
    fake_client.create_secret.assert_called_once_with(Name="new-secret", SecretString="v1")
