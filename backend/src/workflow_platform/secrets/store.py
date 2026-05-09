"""Credential storage abstraction.

Connectors fetch their credentials (API keys, OAuth tokens, AWS access keys,
HMAC secrets) through a `SecretStore`. The default is `EnvSecretStore`, which
reads `os.environ`; production SaaS uses `AwsSecretsManagerStore`. Self-hosted
installations can plug their own store (Vault, etc.) by implementing the ABC.

Secrets are referenced by key (a stable identifier) rather than stored inline
in workflow definitions or repository rows.
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any


class SecretNotFoundError(LookupError):
    pass


class SecretStore(ABC):
    @abstractmethod
    async def get(self, key: str) -> str:
        """Return the secret value at `key`. Raise SecretNotFoundError if absent."""

    @abstractmethod
    async def put(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    async def get_json(self, key: str) -> dict[str, Any]:
        """Convenience: parse the secret as JSON. Useful for OAuth client config."""
        raw = await self.get(key)
        try:
            return dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Secret {key!r} is not valid JSON") from exc


class EnvSecretStore(SecretStore):
    """Reads from `os.environ`. Writes / deletes mutate the process env (so dev
    code can seed secrets at startup; production should not depend on `put`)."""

    async def get(self, key: str) -> str:
        value = os.environ.get(key)
        if value is None:
            raise SecretNotFoundError(f"Secret {key!r} not found in environment")
        return value

    async def put(self, key: str, value: str) -> None:
        os.environ[key] = value

    async def delete(self, key: str) -> None:
        os.environ.pop(key, None)


class AwsSecretsManagerStore(SecretStore):
    """SaaS path: AWS Secrets Manager. Secrets are stored under `key` directly
    (no prefix); use a tenant-scoped naming convention at the caller layer.
    """

    def __init__(self, region: str | None = None) -> None:
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("secretsmanager", region_name=self.region)
        return self._client

    async def get(self, key: str) -> str:
        try:
            response = await asyncio.to_thread(self.client.get_secret_value, SecretId=key)
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundException":
                raise SecretNotFoundError(f"Secret {key!r} not found") from exc
            raise
        if "SecretString" in response:
            return str(response["SecretString"])
        raise ValueError(f"Secret {key!r} is binary; only string secrets are supported")

    async def put(self, key: str, value: str) -> None:
        try:
            await asyncio.to_thread(self.client.put_secret_value, SecretId=key, SecretString=value)
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundException":
                await asyncio.to_thread(self.client.create_secret, Name=key, SecretString=value)
            else:
                raise

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self.client.delete_secret, SecretId=key, ForceDeleteWithoutRecovery=True
        )
