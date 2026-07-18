"""WebSocket endpoint for live status events.

Subscribes the connection to the engine's `EventBus`. Each engine audit append
is mirrored as a JSON event the dashboard renders in real time.

Auth: WebSocket connections cannot use the user-auth middleware. In dev mode,
the client sends `?user=...&groups=...` as query params; in oidc mode, a
`?token=...` query param carries the Bearer JWT, which is validated using the
same `OidcValidator` HTTP requests use. In local mode the browser sends the
session cookie on the upgrade request — no token-in-query-string (query
strings leak into logs). The upgrade is a GET, so the middleware's non-GET
CSRF rule never fires for it: the accept path enforces its own Origin check
(cross-site WebSocket hijacking defense, docs/AUTH_PLAN.md §9.7).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from workflow_platform.auth import OidcValidator, UserIdentity, assign_roles, auth_mode
from workflow_platform.auth.local import SESSION_COOKIE, LocalAuthService
from workflow_platform.auth.middleware import origin_allowed
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


def build_ws_router(
    events: EventBus,
    validator: OidcValidator | None = None,
    local_auth: LocalAuthService | None = None,
) -> APIRouter:
    router = APIRouter()
    ws_validator = validator or OidcValidator()

    @router.websocket("/ws/events")
    async def events_socket(ws: WebSocket) -> None:
        mode = auth_mode()
        if mode == "dev":
            user = _dev_user_from_query(ws)
        elif mode == "local":
            user = None
            token = ws.cookies.get(SESSION_COOKIE)
            if token and local_auth is not None:
                user = await local_auth.authenticate(token)
            if user is not None and not origin_allowed(
                ws.headers.get("origin"), ws.headers.get("host")
            ):
                user = None
        else:
            user = await _oidc_user_from_query(ws, ws_validator)
        if user is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth required")
            return

        await ws.accept()
        queue = events.subscribe()
        # Race the event queue against ws.receive(): receive() is how we
        # notice a client disconnect (or the server closing the socket during
        # shutdown) *promptly*. Blocking on queue.get() alone only detected a
        # dead peer at the next send — on a quiet bus that's never, which left
        # this handler task alive forever and hung uvicorn's --reload/shutdown
        # in "Waiting for background tasks to complete".
        recv_task: asyncio.Task[Any] = asyncio.create_task(ws.receive())
        get_task: asyncio.Task[Any] | None = None
        try:
            while True:
                if get_task is None:
                    get_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {recv_task, get_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if recv_task in done:
                    msg = recv_task.result()  # re-raises on abnormal close
                    if msg.get("type") == "websocket.disconnect":
                        break
                    # Ignore client chatter; keep listening for disconnect.
                    recv_task = asyncio.create_task(ws.receive())
                if get_task in done:
                    event = get_task.result()
                    get_task = None
                    await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            for task in (recv_task, get_task):
                if task is not None and not task.done():
                    task.cancel()
            events.unsubscribe(queue)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    return router
