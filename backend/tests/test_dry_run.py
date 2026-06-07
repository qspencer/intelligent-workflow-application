"""Tests for POST /api/workflows/{id}/dry-run (C6.1)."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.api.workflows import _is_external_tool
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import FileWriteTool, Tool, ToolContext, ToolResult
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
_ADMIN = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}


class _RecordingEmailTool(Tool):
    """Stands in for a real email tool — records calls so a test can assert it
    was NOT invoked during a dry run."""

    name = "email_send"
    description = "send an email"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        self.calls.append(params)
        return ToolResult(content={"sent": True})


def _wf(wf_id: str, tools: list[str]) -> dict[str, Any]:
    return {
        "id": wf_id,
        "name": wf_id,
        "trigger": {"type": "manual"},
        "steps": [{"id": "act", "type": "agentic", "goal": "go", "model": MODEL, "tools": tools}],
        "edges": [],
    }


def _engine(repos: Any, bedrock: FakeBedrock, email: Tool) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool(), email]),
        bedrock=bedrock,
        world=mock_world(),
    )


# --- helper ---


def test_is_external_tool_classification() -> None:
    assert _is_external_tool("email_send")
    assert _is_external_tool("connector_send")
    assert _is_external_tool("browser_navigate")
    assert not _is_external_tool("file_read")
    assert not _is_external_tool("pdf_extract")
    assert not _is_external_tool("image_ocr")


# --- endpoint ---


def test_dry_run_completes_and_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_wf("simple-wf", []))))
    engine = _engine(repos, FakeBedrock([text_response("thought about it")]), _RecordingEmailTool())

    body = (
        TestClient(create_app(repositories=repos, engine=engine))
        .post("/api/workflows/simple-wf/dry-run", headers=_ADMIN)
        .json()
    )
    assert body["dry_run"] is True
    assert body["state"] == "completed"
    assert "MockWorld" in body["sandbox"]

    inst = asyncio.run(repos.instances.get(body["instance_id"]))
    assert inst is not None
    assert inst.context.get("dry_run") is True


def test_dry_run_drops_external_tools_no_email_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_wf("em-wf", ["email_send"]))))
    email = _RecordingEmailTool()
    # The model tries to send an email, then wraps up.
    bedrock = FakeBedrock(
        [
            tool_use_response(tool_uses=[("c1", "email_send", {"to": "x@y.com"})]),
            text_response("done"),
        ]
    )
    engine = _engine(repos, bedrock, email)

    body = (
        TestClient(create_app(repositories=repos, engine=engine))
        .post("/api/workflows/em-wf/dry-run", headers=_ADMIN)
        .json()
    )
    assert body["dry_run"] is True
    assert body["state"] == "completed"
    assert email.calls == []  # email_send was removed from the sandbox catalog


def test_dry_run_rejects_browser_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_wf("br-wf", ["browser_navigate"]))))
    engine = _engine(repos, FakeBedrock([]), _RecordingEmailTool())

    r = TestClient(create_app(repositories=repos, engine=engine)).post(
        "/api/workflows/br-wf/dry-run", headers=_ADMIN
    )
    assert r.status_code == 400
    assert "browser" in r.json()["detail"].lower()


def test_dry_run_unknown_workflow_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    engine = _engine(repos, FakeBedrock([]), _RecordingEmailTool())
    r = TestClient(create_app(repositories=repos, engine=engine)).post(
        "/api/workflows/ghost/dry-run", headers=_ADMIN
    )
    assert r.status_code == 404
