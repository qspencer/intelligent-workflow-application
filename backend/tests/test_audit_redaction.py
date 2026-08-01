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
SECRET_TRIGGER = "SENTINEL-TRIGGER-raw-email-body"

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
                # The model ECHOES both secrets into its final free text
                # (external review F3 round 3 — output_text bypassed the
                # structural redaction).
                text_response(f"Applied. Saw {SECRET_IN} and {SECRET_OUT}."),
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
    # A raw inbound message shape: routing (message_id) + content
    # (subject/body). Below-grant readers must see the id but not the body
    # (TRACE_GOVERNANCE_PLAN F4 — the detail endpoint leaked this before).
    instance = await engine.run(
        definition,
        trigger_payload={
            "message_id": "msg-routing-123",
            "subject": SECRET_TRIGGER,
            "body": SECRET_TRIGGER,
        },
    )
    assert instance.state.value == "completed"
    app = create_app(repositories=repos, engine=engine)
    return TestClient(app), instance.id


async def test_below_admin_cannot_recover_tool_secrets_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, iid = await _run_and_app(monkeypatch)

    surfaces = [
        "/api/audit",
        "/api/workflow-instances",  # the LIST endpoint (external review round 4)
        f"/api/workflow-instances/{iid}/audit",
        f"/api/workflow-instances/{iid}",
        f"/api/workflow-instances/{iid}/steps/act/explain",
    ]
    for headers in (_VIEWER, _USER):
        for path in surfaces:
            body = client.get(path, headers=headers).text
            assert SECRET_IN not in body, f"input secret leaked at {path} for {headers}"
            assert SECRET_OUT not in body, f"output secret leaked at {path} for {headers}"
            assert SECRET_TRIGGER not in body, f"trigger body leaked at {path} for {headers}"

    # Routing survives redaction (kept for the reader; needed by pin/resume).
    viewer_detail = client.get(f"/api/workflow-instances/{iid}", headers=_VIEWER).text
    assert "msg-routing-123" in viewer_detail

    # The LIST endpoint is a summary — it omits the full trace for EVERYONE,
    # including admin-tier (a dashboard list shouldn't ship execution traces).
    admin_list = client.get("/api/workflow-instances", headers=_ADMIN).text
    assert SECRET_IN not in admin_list and SECRET_OUT not in admin_list

    # Admin-tier still gets the raw trace (forensics preserved) — incl. the
    # echoed output_text.
    admin_instance = client.get(f"/api/workflow-instances/{iid}", headers=_ADMIN).text
    assert SECRET_OUT in admin_instance
    assert SECRET_TRIGGER in admin_instance
    admin_explain = client.get(
        f"/api/workflow-instances/{iid}/steps/act/explain", headers=_ADMIN
    ).text
    assert SECRET_IN in admin_explain and SECRET_OUT in admin_explain


def test_redact_projects_trigger_payload_and_recall() -> None:
    """Unit-level: `redact_tool_data` is no longer a no-op on a trigger
    payload or a recall block (TRACE_GOVERNANCE_PLAN F1/F4/F7). A full
    veracium recall run is too heavy for a unit test, so the recall shape is
    exercised directly here; the trigger payload is also covered end-to-end
    above."""
    from workflow_platform.api.redaction import redact_tool_data

    obj = {
        "trigger": {"message_id": "m1", "subject": "SECRET-SUBJ", "body": "SECRET-BODY"},
        "steps": {
            "classify": {
                "output": {
                    "category": "urgent",
                    "recall": "prior thread: SECRET-CORRESPONDENT-HISTORY",
                }
            }
        },
    }
    redacted = redact_tool_data(obj, admin=False)
    import json as _json

    blob = _json.dumps(redacted)
    assert "SECRET-SUBJ" not in blob and "SECRET-BODY" not in blob
    assert "SECRET-CORRESPONDENT-HISTORY" not in blob
    assert redacted["trigger"]["message_id"] == "m1"  # routing kept
    assert redacted["steps"]["classify"]["output"]["category"] == "urgent"  # structured kept

    # admin=True is unchanged (forensics preserved).
    assert redact_tool_data(obj, admin=True) == obj
