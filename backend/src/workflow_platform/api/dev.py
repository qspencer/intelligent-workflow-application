"""Dev-only routes: surface recent backend errors to the dashboard header.

Mounted only when ``AUTH_MODE=dev`` (see ``main.create_app``). Reads the
in-memory :class:`ErrorBuffer` that ``ErrorCaptureHandler`` fills from log
records at ``>= ERROR``. Not part of the production surface — tracebacks can
carry paths/internal detail, so this never ships behind a real IdP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from workflow_platform.auth import current_user
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.observability import ErrorBuffer


def build_dev_router(buffer: ErrorBuffer) -> APIRouter:
    router = APIRouter(prefix="/api/dev")

    @router.get("/errors")
    async def list_errors(_: UserIdentity = Depends(current_user)) -> dict[str, Any]:
        return {
            "total": buffer.total(),
            "distinct": buffer.distinct(),
            "errors": buffer.snapshot(),
        }

    @router.post("/errors/clear")
    async def clear_errors(_: UserIdentity = Depends(current_user)) -> dict[str, str]:
        buffer.clear()
        return {"status": "cleared"}

    return router
