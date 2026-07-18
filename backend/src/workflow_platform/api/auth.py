"""Login/logout endpoints for `AUTH_MODE=local` (docs/AUTH_PLAN.md §5).

Only mounted in local mode. `/api/auth/login` is the single unauthenticated
POST in the app (rate-limited); everything else stays behind the middleware.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from workflow_platform.auth.local import (
    SESSION_COOKIE,
    LocalAuthService,
    LoginRateLimiter,
    canonical_email,
    session_ttl_hours,
)

_FAILED = JSONResponse({"detail": "Invalid email or password"}, status_code=401)


class LoginRequest(BaseModel):
    email: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def build_auth_router(
    local_auth: LocalAuthService, limiter: LoginRateLimiter | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    limiter = limiter or LoginRateLimiter()

    @router.post("/login")
    async def login(body: LoginRequest, request: Request) -> Response:
        ip = _client_ip(request)
        email = canonical_email(body.email)
        for key in (f"ip:{ip}", f"email:{email}"):
            retry_in = limiter.check(key)
            if retry_in is not None:
                return JSONResponse(
                    {"detail": "Too many attempts; try again later"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(retry_in)))},
                )
        token = await local_auth.login(email, body.password, source_ip=ip)
        if token is None:
            limiter.record(f"ip:{ip}")
            limiter.record(f"email:{email}")
            return _FAILED
        response = JSONResponse({"ok": True})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(session_ttl_hours() * 3600),
            httponly=True,
            samesite="lax",
            # Behind the ALB, uvicorn needs --proxy-headers for the scheme
            # to reflect the client connection (AUTH_PLAN §5).
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await local_auth.logout(token)
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    return router
