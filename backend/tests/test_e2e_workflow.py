"""End-to-end test: a small workflow runs against in-memory repos + MockWorld
in well under a second, exercising trigger payload → workflow → step outputs
→ audit trail. Mirrors the Phase 0 success criterion in BUILD_PLAN.md.
"""

from __future__ import annotations

import time

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.tools import FileWriteTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


async def test_pdf_drop_to_filed_summary_under_one_second() -> None:
    """Simulate: trigger payload mentions a dropped PDF; workflow extracts a
    fake document type, then files a summary via the agent's file_write tool.
    """
    repos = in_memory_repositories()
    fns = FunctionRegistry()

    async def fake_extract(
        config: dict[str, object], ctx: object, world: object
    ) -> dict[str, object]:
        # Stand-in for the real pdf_extract; we don't need OCR in this test.
        return {"text": "Invoice from Acme Corp", "is_native": True, "char_count": 22}

    fns.register("fake_extract", fake_extract)

    definition = load_definition(
        {
            "id": "invoice-processing",
            "name": "Invoice Processing",
            "description": "Extract a PDF and file a summary",
            "trigger": {"type": "file_watch", "config": {"folder": "/in", "pattern": "*.pdf"}},
            "steps": [
                {
                    "id": "extract",
                    "type": "deterministic",
                    "function": "fake_extract",
                },
                {
                    "id": "file_summary",
                    "type": "agentic",
                    "goal": "Save a one-line summary to /out/summary.txt",
                    "model": MODEL,
                    "tools": ["file_write"],
                },
            ],
            "edges": [{"from": "extract", "to": "file_summary"}],
        }
    )

    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "c1",
                        "file_write",
                        {"path": "/out/summary.txt", "content": "Invoice from Acme Corp"},
                    )
                ]
            ),
            text_response("Filed the summary."),
        ]
    )
    world = mock_world()
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=bedrock,
        world=world,
    )

    start = time.perf_counter()
    instance = await engine.run(definition, trigger_payload={"file_path": "/in/invoice.pdf"})
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"Workflow took {elapsed:.3f}s — should be <1s"
    assert instance.state == WorkflowInstanceState.COMPLETED

    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files["/out/summary.txt"] == b"Invoice from Acme Corp"

    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert actions[0] == "workflow_started"
    assert "step_started" in actions
    assert "step_completed" in actions
    assert "tool_call" in actions
    assert actions[-1] == "workflow_completed"

    tool_call = next(e for e in audit if e.action == "tool_call")
    assert tool_call.detail["name"] == "file_write"
    assert tool_call.detail["input"]["path"] == "/out/summary.txt"
