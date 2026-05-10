"""Authentication middleware — populates `request.state.user`.

Modes:
- `oidc` (default in production): validate `Authorization: Bearer <jwt>`. Reject
  if missing/invalid. JWTs come from the configured IdP.
- `dev`: accept identity from headers (`X-Dev-User`, `X-Dev-Groups`). For local
  development and tests only — never set in production.

The `/api/health` endpoint is allowed without authentication.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.oidc import OidcValidator
from workflow_platform.auth.rbac import assign_roles

UNAUTHENTICATED_PATHS = {"/api/health", "/metrics", "/openapi.json", "/docs", "/redoc"}
UNAUTHENTICATED_PREFIXES = ("/api/triggers/webhook/",)


def auth_mode() -> str:
    return os.environ.get("AUTH_MODE", "oidc").lower()


def _dev_identity_from_headers(request: Request) -> UserIdentity | None:
    sub = request.headers.get("X-Dev-User")
    if not sub:
        return None
    groups_header = request.headers.get("X-Dev-Groups", "")
    groups = [g.strip() for g in groups_header.split(",") if g.strip()]
    return UserIdentity(
        sub=sub,
        email=request.headers.get("X-Dev-Email"),
        name=request.headers.get("X-Dev-Name"),
        groups=groups,
        roles=assign_roles(groups),
    )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable, validator: OidcValidator | None = None) -> None:  # type: ignore[type-arg]
        super().__init__(app)
        self._validator = validator or OidcValidator()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path in UNAUTHENTICATED_PATHS or path.startswith(UNAUTHENTICATED_PREFIXES):
            return await call_next(request)

        mode = auth_mode()
        if mode == "dev":
            user = _dev_identity_from_headers(request)
            if user is None:
                return JSONResponse(
                    {"detail": "Missing X-Dev-User header (AUTH_MODE=dev)"},
                    status_code=401,
                )
        else:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return JSONResponse({"detail": "Missing Bearer token"}, status_code=401)
            token = header.removeprefix("Bearer ").strip()
            try:
                user = await self._validator.validate(token)
            except Exception as exc:
                return JSONResponse({"detail": f"Invalid token: {exc}"}, status_code=401)

        request.state.user = user
        return await call_next(request)
