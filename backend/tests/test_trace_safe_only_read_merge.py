"""Read-surface raw-merge under the safe-only flip (TG3b.3,
docs/TRACE_GOVERNANCE_PLAN.md §4.4): with `trace_safe_only` on the operational
store is projected, so a GRANT-HOLDER's raw is re-merged from the vault at the
instance-detail read; a below-grant reader still gets the projection."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import RawTraceReasonCode, User, in_memory_repositories
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET_IN = "READMERGE-IN"
SECRET_OUT = "READMERGE-OUT"
TRIG = "READMERGE-TRIGGER-BODY"

_GRANTED = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_BELOW = {"X-Dev-User": "bob", "X-Dev-Groups": "org-users"}


class _SecretTool(Tool):
    name = "leaky_tool"
    description = "returns sensitive content"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content={"text": SECRET_OUT})


def _setup(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([_SecretTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("t1", "leaky_tool", {"body": SECRET_IN})]),
                text_response(f"Saw {SECRET_OUT}"),
            ]
        ),
        world=mock_world(),
        trace_safe_only=True,  # THE FLIP
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

    async def go() -> str:
        instance = await engine.run(definition, trigger_payload={"body": TRIG})
        root = User(iss="dev", sub="root", org_id="default", roles=["Administrator"])
        await repos.users.save(root)
        await repos.users.save(
            User(iss="dev", sub="bob", org_id="default", roles=["Organization User"])
        )
        # grant root platform-wide raw access
        grant = await RawTraceGrantService(repos).request(
            principal_id=root.id,
            org_id="default",
            requested_by="grantor",
            reason_code=RawTraceReasonCode.DEBUGGING,
        )
        assert grant.state.value == "active"
        return instance.id

    iid = asyncio.run(go())
    return TestClient(create_app(repositories=repos, engine=engine)), iid


def test_grant_holder_reads_raw_merged_from_vault_under_flip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, iid = _setup(monkeypatch)
    body = client.get(f"/api/workflow-instances/{iid}", headers=_GRANTED).json()
    assert body["raw_included"] is True
    blob = str(body)
    # raw restored from the vault for the grant-holder
    assert SECRET_IN in blob and SECRET_OUT in blob and TRIG in blob


def test_below_grant_still_projected_under_flip(monkeypatch: pytest.MonkeyPatch) -> None:
    client, iid = _setup(monkeypatch)
    body = client.get(f"/api/workflow-instances/{iid}", headers=_BELOW).json()
    assert body["raw_included"] is False
    blob = str(body)
    assert SECRET_IN not in blob and SECRET_OUT not in blob and TRIG not in blob
