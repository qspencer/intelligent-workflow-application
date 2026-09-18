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

from workflow_platform.api.raw_trace_audit import (
    SURFACE_WS,
    begin_raw_release,
    commit_raw_release,
)
from workflow_platform.api.redaction import project_audit_detail
from workflow_platform.auth import OidcValidator, UserIdentity, assign_roles, auth_mode
from workflow_platform.auth.local import SESSION_COOKIE, LocalAuthService
from workflow_platform.auth.middleware import origin_allowed
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.auth.rbac import Role
from workflow_platform.events import EventBus
from workflow_platform.persistence import Repositories
from workflow_platform.trace_rehydrate import RawTraceRehydrator, RawTraceUnavailable


def event_deliverable(event: dict[str, Any], subscriber_org: str | None) -> bool:
    """Pure WS org-filter primitive (ROLES_PLAN §7.6, test-pinned directly).

    `subscriber_org is None` = unscoped Administrator, receives everything.
    A scoped subscriber receives ONLY events whose `org_id` equals theirs —
    so instance-less/system events (no `org_id`) and foreign-org events are
    both withheld. Fail-closed: a missing/malformed `org_id` never matches.
    """
    if subscriber_org is None:
        return True
    return event.get("org_id") == subscriber_org


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


def _redact_ws_event(event: dict[str, Any]) -> dict[str, Any]:
    """Project raw tool data out of a pushed event for below-admin
    subscribers (external review 2026-08-01 F3) — WS events mirror audit
    entries, incl. `step_completed` carrying step output with tool_calls."""
    detail = event.get("detail")
    if not isinstance(detail, dict):
        return event
    return {**event, "detail": project_audit_detail(event.get("action"), detail)}


async def _rehydrated_ws_event(
    repositories: Repositories, event: dict[str, Any], projected: dict[str, Any]
) -> dict[str, Any]:
    """Restore a vaulted audit detail onto a pushed frame for a grant holder.

    Falls back to the projected frame on any failure: an unrecoverable raw is
    withheld, never faked.
    """
    instance_id = event.get("workflow_instance_id")
    entry_id = event.get("id")
    if not isinstance(instance_id, str) or not isinstance(entry_id, str):
        return projected
    instance = await repositories.instances.get(instance_id)
    if instance is None:
        return projected
    try:
        full = await RawTraceRehydrator(repositories).rehydrate_audit_detail(
            purpose=SURFACE_WS,
            org_id=instance.org_id,
            instance_id=instance_id,
            audit_entry_id=entry_id,
            action=str(event.get("action") or ""),
            stored_detail=event.get("detail"),
            projector_version=str(event.get("projector_version") or "") or None,
        )
    except RawTraceUnavailable:
        return projected
    return {**event, "detail": full}


class _OrgUnresolved(Exception):
    """A non-Administrator subscriber whose org can't be resolved (finding 5)."""


def build_ws_router(
    events: EventBus,
    validator: OidcValidator | None = None,
    local_auth: LocalAuthService | None = None,
    repositories: Repositories | None = None,
) -> APIRouter:
    router = APIRouter()
    ws_validator = validator or OidcValidator()
    grant_service = RawTraceGrantService(repositories) if repositories is not None else None

    async def _subscriber_identity(user: UserIdentity) -> tuple[str | None, str | None]:
        """(org, principal_id) for this subscriber. org is None = unscoped
        (Administrator). FAIL-CLOSED (external review 2026-08-01 finding 5): a
        non-Administrator whose platform user row can't be resolved is
        REJECTED, not assigned "default" — the WS must not manufacture tenant
        membership. Raises `_OrgUnresolved`; the handler closes. `principal_id`
        (the user row id) is None when no row exists — the grant check then
        fails closed to projected traces."""
        row = (
            await repositories.users.get_by_identity(current_issuer(), user.sub)
            if repositories is not None
            else None
        )
        if Role.ADMINISTRATOR.value in user.roles:
            return None, (row.id if row is not None else None)
        if row is None:
            raise _OrgUnresolved(user.sub)
        return row.org_id, row.id

    def _deliver(event: dict[str, Any], subscriber_org: str | None) -> bool:
        return event_deliverable(event, subscriber_org)

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

        try:
            subscriber_org, principal_id = await _subscriber_identity(user)
        except _OrgUnresolved:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="organization unresolved")
            return

        # Raw tool payloads ride WS events too; a subscriber reads them only
        # with a covering raw-trace GRANT (TRACE_GOVERNANCE_PLAN §2, TG1 —
        # NOT a role). An unscoped Administrator (subscriber_org None) needs a
        # platform-wide grant; a scoped subscriber needs one covering their
        # org. The grant is re-evaluated PER raw-bearing frame (external code
        # review 2026-08-02 F9), NOT cached at accept — so a revoked/expired
        # grant stops raw on the next frame.
        async def _covers_now() -> bool:
            return (
                principal_id is not None
                and grant_service is not None
                and (
                    await grant_service.covering(
                        principal_id=principal_id, target_org=subscriber_org
                    )
                )
                is not None
            )

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
                    if _deliver(event, subscriber_org):
                        projected = _redact_ws_event(event)
                        # R13 self-audit: `projected == event` used to mean
                        # "carries no raw". Since the at-rest tightening the
                        # published event is ALREADY the projection, so that
                        # test is true for every audit event and the stream
                        # silently stopped offering raw to grant holders —
                        # the round-12 finding on a third surface. The
                        # persisted stamp is the honest signal: if the raw
                        # was vaulted, this event is raw-bearing.
                        vaulted = event.get("projector_version") is not None
                        if projected == event and not vaulted:
                            # The event carries no raw at all — no grant needed,
                            # no access audit. Send as-is.
                            await ws.send_json(event)
                        elif not await _covers_now():
                            # Below grant NOW (never granted, or revoked/expired
                            # since accept, F9) — send the projected frame.
                            await ws.send_json(projected)
                        else:
                            # A grant-holder receiving a raw-bearing event: emit
                            # the release-boundary audit pair BEFORE the frame is
                            # sent, one pair per delivered raw event (§3.1). A
                            # failed audit degrades THIS frame to projected
                            # without closing the connection.
                            assert repositories is not None  # covers_now ⟹ repos
                            # R13 finding 2: this used the ATOMIC release,
                            # which commits `released` before anything is
                            # fetched. With the vault row removed the socket
                            # correctly sent a withheld frame while the
                            # release record still said `released`, and a
                            # separate system-access record said
                            # `retrieval_failed` — the two disagreed. WS is a
                            # vault-fetch surface like HTTP: attempt,
                            # retrieve, then record what actually happened.
                            ws_instance = event.get("workflow_instance_id")
                            request_id, _rr = await begin_raw_release(
                                repositories,
                                raw_ok=True,
                                surface=SURFACE_WS,
                                actor_id=user.sub,
                                instance_id=ws_instance,
                                kinds=("tool_calls", "output_text"),
                            )
                            if request_id is None:
                                await ws.send_json(projected)
                                continue
                            frame = event
                            returned = ["tool_calls", "output_text"]
                            withheld: list[str] = []
                            if vaulted:
                                frame = await _rehydrated_ws_event(repositories, event, projected)
                                if frame is projected:
                                    returned, withheld = [], ["audit_detail"]
                                else:
                                    returned = ["audit_detail"]
                            audit_ok, _rr = await commit_raw_release(
                                repositories,
                                request_id=request_id,
                                surface=SURFACE_WS,
                                actor_id=user.sub,
                                instance_id=ws_instance,
                                returned_kinds=returned,
                                withheld_kinds=withheld,
                            )
                            await ws.send_json(frame if audit_ok else projected)
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
