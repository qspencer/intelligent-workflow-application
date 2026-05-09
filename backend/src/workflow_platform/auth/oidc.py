"""OIDC token validation.

Validates JWTs against a JWKS endpoint configured via env vars:
- `OIDC_ISSUER`
- `OIDC_AUDIENCE`
- `OIDC_JWKS_URL`

The synchronous JWKS fetch (PyJWKClient) is wrapped in `asyncio.to_thread` so
it doesn't block the FastAPI event loop. Keys are cached by PyJWKClient.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import jwt
from jwt import PyJWKClient

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.rbac import assign_roles


class OidcConfig:
    def __init__(
        self,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_url: str | None = None,
    ) -> None:
        self.issuer = issuer or os.environ.get("OIDC_ISSUER", "")
        self.audience = audience or os.environ.get("OIDC_AUDIENCE", "")
        self.jwks_url = jwks_url or os.environ.get("OIDC_JWKS_URL", "")

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url)


class OidcValidator:
    def __init__(self, config: OidcConfig | None = None) -> None:
        self.config = config or OidcConfig()
        self._jwks_client: PyJWKClient | None = None

    @property
    def jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            if not self.config.configured:
                raise RuntimeError(
                    "OIDC is not configured. Set OIDC_ISSUER / OIDC_AUDIENCE / OIDC_JWKS_URL."
                )
            self._jwks_client = PyJWKClient(self.config.jwks_url, cache_keys=True)
        return self._jwks_client

    async def validate(self, token: str) -> UserIdentity:
        signing_key = await asyncio.to_thread(self.jwks_client.get_signing_key_from_jwt, token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.config.audience,
            issuer=self.config.issuer,
        )
        return _identity_from_claims(claims)


def _identity_from_claims(claims: dict[str, Any]) -> UserIdentity:
    groups: list[str] = []
    raw_groups = claims.get("groups")
    if isinstance(raw_groups, list):
        groups = [str(g) for g in raw_groups]
    roles = assign_roles(groups)
    return UserIdentity(
        sub=str(claims["sub"]),
        email=claims.get("email"),
        name=claims.get("name"),
        groups=groups,
        roles=roles,
    )
