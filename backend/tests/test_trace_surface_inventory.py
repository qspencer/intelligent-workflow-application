"""P2 — the total raw-surface inventory (external code re-review 2026-08-03,
finding 2). Raw-capable response fields on surfaces that build their own
responses OUTSIDE the projector were returning raw to below-grant readers:
step explain (a no-tool step's `output_text`, and a deterministic step's
`output`), `/api/escalations` (`reason` + `context`), dry-run and run-batch
(`error` / `str(exc)`). Each test reproduces the reviewer's bypass."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    RawTraceReasonCode,
    StepExecution,
    StepExecutionState,
    User,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)

SECRET = "SURFACE-RAW-SENTINEL"
_GRANTED = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
# An ORG-SCOPED grant holder. The escalations list is org-scoped, so an
# unscoped Administrator (scope.org_id None) would need a PLATFORM-WIDE grant —
# the established rule for cross-org surfaces; an org-scoped reader is the
# right shape for testing the org-scoped release.
_GRANTED_ORG = {"X-Dev-User": "oa", "X-Dev-Groups": "org-admins"}
_VIEWER = {"X-Dev-User": "val", "X-Dev-Groups": "org-viewers"}


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    app = create_app(repositories=repos, start_triggers=False)
    return TestClient(app), repos


async def _seed_users_and_grant(repos: Any) -> None:
    root = User(iss="dev", sub="root", org_id="default", roles=["Administrator"])
    await repos.users.save(root)
    await repos.users.save(
        User(iss="dev", sub="val", org_id="default", roles=["Organization Viewer"])
    )
    oa = User(iss="dev", sub="oa", org_id="default", roles=["Organization Administrator"])
    await repos.users.save(oa)
    svc = RawTraceGrantService(repos)
    for principal in (root.id, oa.id):
        await svc.request(
            principal_id=principal,
            org_id="default",
            requested_by="grantor",
            reason_code=RawTraceReasonCode.DEBUGGING,
        )


async def _seed_instance(repos: Any, *, output: dict[str, Any], step_id: str = "s1") -> str:
    inst = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.COMPLETED)
    )
    await repos.steps.create(
        StepExecution(
            instance_id=inst.id,
            step_id=step_id,
            attempt=1,
            state=StepExecutionState.COMPLETED,
            output=output,
        )
    )
    return str(inst.id)


# --- explain: free-form output_text is raw BY TAINT, tool or no tool ---


def test_explain_no_tool_output_text_is_not_released_below_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)

    async def go() -> str:
        await _seed_users_and_grant(repos)
        # an agentic step that called NO tool — the reviewer's exact repro
        return await _seed_instance(repos, output={"output_text": SECRET, "usage": {}})

    iid = asyncio.run(go())
    body = client.get(f"/api/workflow-instances/{iid}/steps/s1/explain", headers=_VIEWER).json()
    assert body["raw_included"] is False
    assert SECRET not in str(body)


def test_explain_deterministic_output_is_projected_below_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)

    async def go() -> str:
        await _seed_users_and_grant(repos)
        # deterministic output is NOT automatically safe
        return await _seed_instance(repos, output={"summary": SECRET})

    iid = asyncio.run(go())
    body = client.get(f"/api/workflow-instances/{iid}/steps/s1/explain", headers=_VIEWER).json()
    assert SECRET not in str(body)


def test_explain_releases_to_a_grant_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)

    async def go() -> str:
        await _seed_users_and_grant(repos)
        return await _seed_instance(repos, output={"output_text": SECRET, "usage": {}})

    iid = asyncio.run(go())
    body = client.get(f"/api/workflow-instances/{iid}/steps/s1/explain", headers=_GRANTED).json()
    assert body["raw_included"] is True
    assert SECRET in str(body)  # forensics preserved under the grant


# --- escalations: agent-authored reason/context are raw ---


def test_escalation_reason_and_context_are_grant_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)

    async def go() -> None:
        await _seed_users_and_grant(repos)
        iid = await _seed_instance(repos, output={})
        await repos.audit.append(
            AuditEntry(
                actor_type="engine",
                actor_id="agent:act",
                action="escalation_requested",
                workflow_instance_id=iid,
                detail={"reason": SECRET, "context": {"body": SECRET}},
            )
        )

    asyncio.run(go())

    below = client.get("/api/escalations", headers=_VIEWER).json()
    assert below and below[0]["raw_included"] is False
    assert SECRET not in str(below)

    granted = client.get("/api/escalations", headers=_GRANTED_ORG).json()
    assert granted and granted[0]["raw_included"] is True
    assert SECRET in str(granted)


def test_escalation_release_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)

    async def go() -> None:
        await _seed_users_and_grant(repos)
        iid = await _seed_instance(repos, output={})
        await repos.audit.append(
            AuditEntry(
                actor_type="engine",
                actor_id="agent:act",
                action="escalation_requested",
                workflow_instance_id=iid,
                detail={"reason": SECRET, "context": {}},
            )
        )

    asyncio.run(go())
    client.get("/api/escalations", headers=_GRANTED_ORG)

    async def decided() -> list[Any]:
        return [
            e
            for e in await repos.audit.list_recent(limit=50)
            if e.action == "raw_trace_release_decided" and e.detail.get("surface") == "escalation"
        ]

    assert asyncio.run(decided())
