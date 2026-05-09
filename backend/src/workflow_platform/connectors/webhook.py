"""WebhookConnector — outbound HTTP destination.

The inbound webhook trigger lives at `workflow_platform.triggers.webhook`
(POSTs from the outside world that fire workflows). This connector is the
*outbound* counterpart: agents call `send` to POST a payload to a configured
URL, or `query` to GET. Auth is a header bag fetched from a `SecretStore`.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from workflow_platform.connectors.base import Connector
from workflow_platform.secrets import SecretNotFoundError, SecretStore


class WebhookConnector(Connector):
    type: ClassVar[str] = "webhook"

    def __init__(
        self,
        *,
        send_url: str | None = None,
        query_url: str | None = None,
        health_url: str | None = None,
        auth_header_secret: str | None = None,
        secret_store: SecretStore | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.send_url = send_url
        self.query_url = query_url
        self.health_url = health_url or send_url or query_url
        self.auth_header_secret = auth_header_secret
        self.secret_store = secret_store
        self.timeout_seconds = timeout_seconds

    async def authenticate(self) -> None:
        if self.auth_header_secret and self.secret_store is not None:
            # Fetch once to verify the secret exists. Connectors don't cache —
            # secrets may rotate between calls.
            try:
                await self.secret_store.get(self.auth_header_secret)
            except SecretNotFoundError as exc:
                raise RuntimeError(
                    f"Webhook auth secret {self.auth_header_secret!r} is missing"
                ) from exc

    async def _headers(self) -> dict[str, str]:
        if not self.auth_header_secret or self.secret_store is None:
            return {}
        try:
            value = await self.secret_store.get(self.auth_header_secret)
        except SecretNotFoundError:
            return {}
        # Convention: secret is "<HeaderName>: <Value>"; multiple headers
        # separated by newlines.
        headers: dict[str, str] = {}
        for line in value.splitlines():
            if ":" not in line:
                continue
            name, _, val = line.partition(":")
            headers[name.strip()] = val.strip()
        return headers

    async def health_check(self) -> bool:
        if not self.health_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(self.health_url, headers=await self._headers())
            return 200 <= response.status_code < 500
        except httpx.HTTPError:
            return False

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.send_url:
            raise RuntimeError("WebhookConnector.send requires send_url")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.send_url, json=payload, headers=await self._headers())
        return {
            "status_code": response.status_code,
            "body": _safe_json(response) or response.text[:1000],
        }

    async def query(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.query_url:
            raise RuntimeError("WebhookConnector.query requires query_url")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                self.query_url, params=params, headers=await self._headers()
            )
        return {
            "status_code": response.status_code,
            "body": _safe_json(response) or response.text[:1000],
        }


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
