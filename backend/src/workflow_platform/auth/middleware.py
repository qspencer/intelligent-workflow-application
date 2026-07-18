"""Authentication middleware — populates `request.state.user`.

Modes:
- `oidc` (default in production): validate `Authorization: Bearer <jwt>`. Reject
  if missing/invalid. JWTs come from the configured IdP.
- `local` (docs/AUTH_PLAN.md): validate the `wp_session` cookie against the
  server-side session store. First-party email+password login for self-hosted
  deployments. Dev headers are ignored entirely in this mode.
- `dev`: accept identity from headers (`X-Dev-User`, `X-Dev-Groups`). For local
  development and tests only — never set in production.

The `/api/health` endpoint is allowed without authentication.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from workflow_platform.auth.identity import UserIdentity

if TYPE_CHECKING:
    from workflow_platform.auth.local import LocalAuthService
    from workflow_platform.auth.provisioning import UserProvisioner
from workflow_platform.auth.oidc import OidcValidator
from workflow_platform.auth.rbac import assign_roles

UNAUTHENTICATED_PATHS = {
    "/api/health",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/api/auth/login",
}
UNAUTHENTICATED_PREFIXES = ("/api/triggers/webhook/",)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def auth_mode() -> str:
    return os.environ.get("AUTH_MODE", "oidc").lower()


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """CSRF check for cookie-authenticated state changes: when the browser
    sends an Origin, its host must match the request Host (or an entry in
    AUTH_ALLOWED_ORIGINS — e.g. a dev-server proxy). Absent Origin passes:
    non-browser clients don't send one, and browsers do on cross-site."""
    if not origin:
        return True
    if origin == "null":  # sandboxed / opaque cross-site origin
        return False
    netloc = urlparse(origin).netloc
    if host and netloc == host:
        return True
    extra = os.environ.get("AUTH_ALLOWED_ORIGINS", "")
    return netloc in {o.strip() for o in extra.split(",") if o.strip()}


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
    def __init__(
        self,
        app: Callable,  # type: ignore[type-arg]
        validator: OidcValidator | None = None,
        provisioner: UserProvisioner | None = None,
        local_auth: LocalAuthService | None = None,
    ) -> None:
        super().__init__(app)
        self._validator = validator or OidcValidator()
        # JIT user persistence (auth/provisioning.py). Optional so tests and
        # embedded uses without repositories keep working.
        self._provisioner = provisioner
        self._local_auth = local_auth

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
        elif mode == "local":
            from workflow_platform.auth.local import SESSION_COOKIE

            token = request.cookies.get(SESSION_COOKIE)
            user = None
            if token and self._local_auth is not None:
                user = await self._local_auth.authenticate(token)
            if user is None:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            if request.method not in _SAFE_METHODS and not origin_allowed(
                request.headers.get("origin"), request.headers.get("host")
            ):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
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
        # JIT provisioning is for IdP-born identities only. Local-mode users
        # already ARE rows — provisioning them again would mint duplicates
        # under the oidc issuer (AUTH_PLAN §5, design-review finding).
        if self._provisioner is not None and mode != "local":
            await self._provisioner.provision(user)
        return await call_next(request)
