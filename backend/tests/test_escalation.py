"""Tests for request_human_review tool + /api/escalations endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.events import EventBus
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    in_memory_repositories,
)
from workflow_platform.persistence.models import _new_id, _utcnow
from workflow_platform.tools import RequestHumanReviewTool, ToolContext

# --- tool ---


async def test_request_human_review_writes_audit_and_returns_id() -> None:
    repos = in_memory_repositories()
    tool = RequestHumanReviewTool(repos.audit)
    ctx = ToolContext(workflow_instance_id="i-1", agent_id="agent:act")

    result = await tool.execute(
        {"reason": "Blocked: missing vendor mapping", "context": {"vendor": "ACME"}},
        context=ctx,
    )
    assert result.ok
    assert result.content["status"] == "pending"
    eid = result.content["escalation_id"]

    audit = await repos.audit.list_recent()
    assert len(audit) == 1
    assert audit[0].id == eid
    assert audit[0].action == "escalation_requested"
    assert audit[0].workflow_instance_id == "i-1"
    assert audit[0].detail["reason"].startswith("Blocked")
    assert audit[0].detail["context"] == {"vendor": "ACME"}


async def test_request_human_review_publishes_event() -> None:
    repos = in_memory_repositories()
    bus = EventBus()
    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for event in bus.stream():
            received.append(event)
            return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)

    tool = RequestHumanReviewTool(repos.audit, events=bus)
    await tool.execute({"reason": "help"})
    await asyncio.wait_for(consumer, timeout=1.0)

    assert received[0]["action"] == "escalation_requested"


async def test_request_human_review_validates_reason() -> None:
    repos = in_memory_repositories()
    tool = RequestHumanReviewTool(repos.audit)
    result = await tool.execute({"reason": "  "})
    assert not result.ok
    assert result.error is not None
    assert "reason" in result.error.lower()


# --- API endpoints ---


def _seed_escalation(repos: Any, *, instance_id: str = "i-1") -> str:
    """Create a pending escalation directly via the audit repo."""
    eid = _new_id()

    async def _do() -> None:
        await repos.audit.append(
            AuditEntry(
                id=eid,
                timestamp=_utcnow(),
                actor_type="agent",
                actor_id="agent:test",
                action="escalation_requested",
                workflow_instance_id=instance_id,
                detail={"reason": "I'm stuck", "context": {}},
            )
        )

    asyncio.run(_do())
    return eid


def _operator() -> dict[str, str]:
    return {"X-Dev-User": "alice", "X-Dev-Groups": "operators"}


def _viewer() -> dict[str, str]:
    return {"X-Dev-User": "bob", "X-Dev-Groups": "viewers"}


@pytest.fixture
def dev_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    app = create_app(repositories=repos)
    return TestClient(app), repos


def test_list_escalations_pending_only_by_default(
    dev_app: tuple[TestClient, Any],
) -> None:
    client, repos = dev_app
    eid_pending = _seed_escalation(repos)
    eid_resolved = _seed_escalation(repos, instance_id="i-2")
    # Resolve the second one.
    asyncio.run(
        repos.audit.append(
            AuditEntry(
                id=_new_id(),
                timestamp=_utcnow(),
                actor_type="human",
                actor_id="alice",
                action="escalation_resolved",
                workflow_instance_id="i-2",
                detail={"original_id": eid_resolved, "resolution": "fixed"},
            )
        )
    )

    r = client.get("/api/escalations", headers=_viewer())
    assert r.status_code == 200
    data = r.json()
    assert {e["id"] for e in data} == {eid_pending}


def test_list_escalations_state_all_includes_resolved(
    dev_app: tuple[TestClient, Any],
) -> None:
    client, repos = dev_app
    e1 = _seed_escalation(repos)
    e2 = _seed_escalation(repos, instance_id="i-2")
    asyncio.run(
        repos.audit.append(
            AuditEntry(
                id=_new_id(),
                timestamp=_utcnow(),
                actor_type="human",
                actor_id="alice",
                action="escalation_resolved",
                workflow_instance_id="i-2",
                detail={"original_id": e2, "resolution": "ok"},
            )
        )
    )
    r = client.get("/api/escalations?state=all", headers=_viewer())
    data = r.json()
    by_id = {e["id"]: e for e in data}
    assert by_id[e1]["resolved"] is False
    assert by_id[e2]["resolved"] is True


def test_resolve_escalation_appends_audit(dev_app: tuple[TestClient, Any]) -> None:
    client, repos = dev_app
    eid = _seed_escalation(repos)
    r = client.post(
        f"/api/escalations/{eid}/resolve",
        json={"resolution": "manual fix applied"},
        headers=_operator(),
    )
    assert r.status_code == 200
    assert r.json() == {"status": "resolved", "escalation_id": eid}

    audit = asyncio.run(repos.audit.list_recent())
    resolved = [e for e in audit if e.action == "escalation_resolved"]
    assert len(resolved) == 1
    assert resolved[0].detail["original_id"] == eid
    assert resolved[0].detail["resolution"] == "manual fix applied"


def test_resolve_unknown_escalation_returns_404(
    dev_app: tuple[TestClient, Any],
) -> None:
    client, _repos = dev_app
    r = client.post("/api/escalations/missing/resolve", json={}, headers=_operator())
    assert r.status_code == 404


def test_resolve_already_resolved_returns_400(
    dev_app: tuple[TestClient, Any],
) -> None:
    client, repos = dev_app
    eid = _seed_escalation(repos)
    asyncio.run(
        repos.audit.append(
            AuditEntry(
                id=_new_id(),
                timestamp=_utcnow(),
                actor_type="human",
                actor_id="alice",
                action="escalation_resolved",
                workflow_instance_id="i-1",
                detail={"original_id": eid, "resolution": "first time"},
            )
        )
    )
    r = client.post(f"/api/escalations/{eid}/resolve", json={}, headers=_operator())
    assert r.status_code == 400


def test_resolve_requires_operator_role(dev_app: tuple[TestClient, Any]) -> None:
    client, repos = dev_app
    eid = _seed_escalation(repos)
    r = client.post(f"/api/escalations/{eid}/resolve", json={}, headers=_viewer())
    assert r.status_code == 403
