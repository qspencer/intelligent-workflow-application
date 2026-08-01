"""TG2 release-boundary audit (docs/TRACE_GOVERNANCE_PLAN.md §3.1): a raw
release to a grant-holder emits an append-only attempt+release pair under one
correlation id, BEFORE the bytes leave; a below-grant read emits neither; a
failed access-audit fails closed (raw withheld, explicit reason)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.events import EventBus
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    RawTraceReasonCode,
    StepExecution,
    User,
    WorkflowInstance,
    in_memory_repositories,
)

_GRANTED = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_UNGRANTED = {"X-Dev-User": "bob", "X-Dev-Groups": "org-users"}
SECRET = "RELEASE-AUDIT-SECRET-body"
_ACCESS_ACTIONS = {"raw_trace_access_attempted", "raw_trace_release_decided"}


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, EventBus]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    events = EventBus()

    async def seed() -> None:
        root = User(iss="dev", sub="root", org_id="default", roles=["Administrator"])
        await repos.users.save(root)
        await repos.users.save(
            User(iss="dev", sub="bob", org_id="default", roles=["Organization User"])
        )
        await repos.instances.create(WorkflowInstance(id="i1", workflow_id="wf", org_id="default"))
        # The secret rides a step output's tool_calls, so the instance-detail
        # surface actually has raw to release (the detail endpoint returns
        # steps + context, not audit entries).
        await repos.steps.create(
            StepExecution(
                id="i1-s1",
                instance_id="i1",
                step_id="act",
                state="completed",
                output={
                    "tool_calls": [
                        {"name": "file_read", "input": {"body": SECRET}, "result": {"content": "x"}}
                    ]
                },
            )
        )
        # Give root a PLATFORM-WIDE grant (covers the unscoped-Administrator WS
        # subscriber too); two distinct admins authorize.
        svc = RawTraceGrantService(repos)
        pending = await svc.request(
            principal_id=root.id,
            org_id=None,
            requested_by="grantor-1",
            reason_code=RawTraceReasonCode.DEBUGGING,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        active = await svc.approve(grant_id=pending.id, approved_by="grantor-2")
        assert active.state.value == "active"

    asyncio.run(seed())
    return TestClient(create_app(repositories=repos, events=events)), repos, events


def _access_events(repos: Any) -> list[AuditEntry]:
    entries = asyncio.run(repos.audit.list_recent(limit=500))
    return [e for e in entries if e.action in _ACCESS_ACTIONS]


def test_grant_holder_read_emits_attempt_release_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _app(monkeypatch)
    resp = client.get("/api/workflow-instances/i1", headers=_GRANTED).json()
    assert resp["raw_included"] is True
    assert SECRET in str(resp)  # raw released to the grant-holder

    events = _access_events(repos)
    attempted = [e for e in events if e.action == "raw_trace_access_attempted"]
    released = [e for e in events if e.action == "raw_trace_release_decided"]
    assert len(attempted) == 1 and len(released) == 1
    # one correlation id across the pair; surface recorded; NO raw content
    rid = attempted[0].detail["request_id"]
    assert released[0].detail["request_id"] == rid
    assert attempted[0].detail["surface"] == "detail"
    assert released[0].detail["outcome"] == "released"
    assert SECRET not in str(attempted[0].detail) and SECRET not in str(released[0].detail)


def test_below_grant_read_emits_no_access_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _app(monkeypatch)
    resp = client.get("/api/workflow-instances/i1", headers=_UNGRANTED).json()
    assert resp["raw_included"] is False
    assert SECRET not in str(resp)
    # an ordinary projected read is not a raw-access event
    assert _access_events(repos) == []


def test_failed_access_audit_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _app(monkeypatch)

    class _FailRawAudit:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def append(self, entry: AuditEntry) -> Any:
            if entry.action in _ACCESS_ACTIONS:
                raise RuntimeError("audit backend down")
            return await self._inner.append(entry)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    repos.audit = _FailRawAudit(repos.audit)

    resp = client.get("/api/workflow-instances/i1", headers=_GRANTED).json()
    # the access audit can't be recorded → raw is withheld (fail-closed)
    assert resp["raw_included"] is False
    assert resp["redaction_reason"] == "access_audit_unavailable"
    assert SECRET not in str(resp)


def test_ws_raw_event_emits_release_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, events = _app(monkeypatch)
    raw_event = {
        "action": "step_completed",
        "org_id": "default",
        "workflow_instance_id": "i1",
        "detail": {"output": {"tool_calls": [{"name": "t", "result": {"content": SECRET}}]}},
    }
    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        asyncio.run(events.publish(dict(raw_event)))
        got = ws.receive_json()
        assert SECRET in str(got)  # grant-holder receives raw
    released = [e for e in _access_events(repos) if e.action == "raw_trace_release_decided"]
    assert len(released) == 1 and released[0].detail["surface"] == "ws"
