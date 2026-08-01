"""End-to-end F3 (external review 2026-08-01): run a REAL agentic tool call
carrying sentinel secrets, then prove a below-admin reader cannot recover
them from ANY read surface — audit list, instance audit, the instance
endpoint (step outputs), or explain — while admin-tier can."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET_IN = "SENTINEL-INPUT-victim@example.com"
SECRET_OUT = "SENTINEL-OUTPUT-private-body"

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_VIEWER = {"X-Dev-User": "v", "X-Dev-Groups": "org-viewers"}
_USER = {"X-Dev-User": "u", "X-Dev-Groups": "org-users"}


class _SecretTool(Tool):
    name = "leaky_tool"
    description = "returns sensitive content"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content={"text": SECRET_OUT})


async def _run_and_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([_SecretTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("t1", "leaky_tool", {"body": SECRET_IN})]),
                text_response("done"),
            ]
        ),
        world=mock_world(),
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "call the tool",
                    "model": "claude-haiku-4-5",
                    "tools": ["leaky_tool"],
                }
            ],
            "edges": [],
        }
    )
    instance = await engine.run(definition, trigger_payload={})
    assert instance.state.value == "completed"
    app = create_app(repositories=repos, engine=engine)
    return TestClient(app), instance.id


async def test_below_admin_cannot_recover_tool_secrets_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, iid = await _run_and_app(monkeypatch)

    surfaces = [
        "/api/audit",
        f"/api/workflow-instances/{iid}/audit",
        f"/api/workflow-instances/{iid}",
        f"/api/workflow-instances/{iid}/steps/act/explain",
    ]
    for headers in (_VIEWER, _USER):
        for path in surfaces:
            body = client.get(path, headers=headers).text
            assert SECRET_IN not in body, f"input secret leaked at {path} for {headers}"
            assert SECRET_OUT not in body, f"output secret leaked at {path} for {headers}"

    # Admin-tier still gets the raw trace (forensics preserved).
    admin_instance = client.get(f"/api/workflow-instances/{iid}", headers=_ADMIN).text
    assert SECRET_OUT in admin_instance
    admin_explain = client.get(
        f"/api/workflow-instances/{iid}/steps/act/explain", headers=_ADMIN
    ).text
    assert SECRET_IN in admin_explain
