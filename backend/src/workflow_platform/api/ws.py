"""WebSocket endpoint for live status events.

Subscribes the connection to the engine's `EventBus`. Each engine audit append
is mirrored as a JSON event the dashboard renders in real time.

Auth: WebSocket connections cannot use the user-auth middleware. In dev mode,
the client sends `?user=...&groups=...` as query params; in oidc mode, a
`?token=...` query param carries the Bearer JWT, which is validated using the
same `OidcValidator` HTTP requests use.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from workflow_platform.auth import OidcValidator, UserIdentity, assign_roles, auth_mode
from workflow_platform.events import EventBus


def _dev_user_from_query(ws: WebSocket) -> UserIdentity | None:
    sub = ws.query_params.get("user")
    if not sub:
        return None
    groups_raw = ws.query_params.get("groups", "")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    return UserIdentity(sub=sub, groups=groups, roles=assign_roles(groups))


async def _oidc_user_from_query(ws: WebSocket, validator: OidcValidator) -> UserIdentity | None:
    token = ws.query_params.get("token")
    if not token:
        return None
    try:
        return await validator.validate(token)
    except Exception:
        return None


def build_ws_router(events: EventBus, validator: OidcValidator | None = None) -> APIRouter:
    router = APIRouter()
    ws_validator = validator or OidcValidator()

    @router.websocket("/ws/events")
    async def events_socket(ws: WebSocket) -> None:
        if auth_mode() == "dev":
            user = _dev_user_from_query(ws)
        else:
            user = await _oidc_user_from_query(ws, ws_validator)
        if user is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth required")
            return

        await ws.accept()
        queue = events.subscribe()
        try:
            while True:
                event = await queue.get()
                try:
                    await ws.send_json(event)
                except (WebSocketDisconnect, RuntimeError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            events.unsubscribe(queue)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    return router
