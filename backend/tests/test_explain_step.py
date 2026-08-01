"""Tests for GET /api/workflow-instances/{id}/steps/{step_id}/explain (C6.4)."""

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
from workflow_platform.workflow import load_definition

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_WF = {
    "id": "wf",
    "name": "WF",
    "trigger": {"type": "manual"},
    "steps": [
        {
            "id": "classify",
            "type": "agentic",
            "goal": "Classify the document",
            "model": HAIKU,
            "tools": ["file_read"],
        },
        {"id": "route", "type": "deterministic", "function": "route_files"},
    ],
    "edges": [{"from": "classify", "to": "route"}],
}

# Forensic explain content needs a raw-trace GRANT (TG1); these tests
# verify the raw view a grant-holder sees.
_H = {"X-Dev-User": "a", "X-Dev-Groups": "org-admins"}


def _grant_admin_raw(repos: Any) -> None:
    """Seed user 'a' + an active org-'default' raw-trace grant (the seeded
    instance is org 'default')."""

    async def go() -> None:
        await repos.users.save(
            User(iss="dev", sub="a", org_id="default", roles=["Organization Administrator"])
        )
        row = await repos.users.get_by_identity("dev", "a")
        assert row is not None
        await RawTraceGrantService(repos).request(
            principal_id=row.id,
            org_id="default",
            requested_by="grantor",
            reason_code=RawTraceReasonCode.DEBUGGING,
        )

    asyncio.run(go())


def _seed(repos: Any) -> str:
    async def go() -> str:
        await repos.definitions.save(load_definition(_WF))
        inst = WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.COMPLETED)
        await repos.instances.create(inst)
        await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="classify",
                state=StepExecutionState.COMPLETED,
                output={
                    "output_text": "It is an invoice.",
                    "model": HAIKU,
                    "cost_usd": 0.002,
                    "memory_hash": "sha256:abc123",
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                        "iterations": 2,
                    },
                    "tool_calls": [
                        {
                            "name": "file_read",
                            "input": {"path": "/inbox/x.pdf"},
                            "result": {"text": "hi"},
                        }
                    ],
                },
            )
        )
        await repos.audit.append(
            AuditEntry(
                actor_type="agent",
                actor_id="wf/classify",
                action="tool_call",
                workflow_instance_id=inst.id,
                step_id="classify",
                detail={
                    "name": "file_read",
                    "input": {"path": "/inbox/x.pdf"},
                    "result": {"text": "hi"},
                },
            )
        )
        await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="route",
                state=StepExecutionState.COMPLETED,
                output={"copied_to": "/out/invoice/x.pdf"},
            )
        )
        return inst.id

    return asyncio.run(go())


def _client(repos: Any) -> TestClient:
    return TestClient(create_app(repositories=repos))


def test_explain_agentic_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    inst_id = _seed(repos)
    _grant_admin_raw(repos)

    body = (
        _client(repos)
        .get(f"/api/workflow-instances/{inst_id}/steps/classify/explain", headers=_H)
        .json()
    )

    assert body["kind"] == "agentic"
    assert body["model"] == HAIKU
    assert body["memory_hash"] == "sha256:abc123"
    assert body["iterations"] == 2
    assert body["cost_usd"] == 0.002
    assert "invoice" in body["output_text"]
    assert "Classify the document" in body["goal"]

    assert len(body["tool_calls"]) == 1
    tc = body["tool_calls"][0]
    assert tc["name"] == "file_read"
    assert "/inbox/x.pdf" in tc["input"]
    assert "hi" in tc["result"]
    assert tc["timestamp"] is not None


def test_explain_deterministic_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    inst_id = _seed(repos)

    body = (
        _client(repos)
        .get(f"/api/workflow-instances/{inst_id}/steps/route/explain", headers=_H)
        .json()
    )
    assert body["kind"] == "deterministic"
    assert body["function"] == "route_files"
    assert "copied_to" in body["output"]
    assert "tool_calls" not in body  # deterministic steps carry no tool calls


def test_explain_unknown_instance_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    r = _client(in_memory_repositories()).get(
        "/api/workflow-instances/ghost/steps/classify/explain", headers=_H
    )
    assert r.status_code == 404


def test_explain_unknown_step_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    inst_id = _seed(repos)
    r = _client(repos).get(f"/api/workflow-instances/{inst_id}/steps/ghost/explain", headers=_H)
    assert r.status_code == 404
