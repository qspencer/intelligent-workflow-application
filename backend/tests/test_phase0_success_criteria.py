"""Phase 0 success-criteria verification.

Three criteria from BUILD_PLAN.md:

1. Drop a PDF into a watched folder → workflow runs → results land in mock world
   → audit log shows every action.
2. The same workflow runs as a deterministic replay test in CI in <1 second.
3. A second engineer can read the code and add a new tool in under a day.
   (Qualitative — see test_easy_tool_addition for a concrete demonstration.)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

import fitz
import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
    pdf_extract,
)
from workflow_platform.persistence import (
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.tools import FileWriteTool, Tool, ToolContext, ToolResult
from workflow_platform.triggers import FilesystemTrigger
from workflow_platform.workflow import load_definition
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


# --- Criterion 1: end-to-end PDF drop → workflow → mock world + audit log ---


async def test_criterion_1_pdf_drop_to_mock_world_and_audit(tmp_path: Path) -> None:
    """A PDF dropped in a watched folder triggers a workflow that extracts the
    PDF, has an agent file a summary into the MockWorld, and records every
    action in the audit log."""
    repos = in_memory_repositories()
    fns = FunctionRegistry()
    fns.register("pdf_extract", pdf_extract)

    definition = load_definition(
        {
            "id": "phase0-invoice",
            "name": "Phase 0 invoice processing",
            "trigger": {
                "type": "file_watch",
                "config": {"folder": str(tmp_path), "pattern": "*.pdf"},
            },
            "steps": [
                {
                    "id": "extract",
                    "type": "deterministic",
                    "function": "pdf_extract",
                    "config": {"filepath_from": "trigger.file_path"},
                },
                {
                    "id": "summarize",
                    "type": "agentic",
                    "goal": "Save the extracted invoice text to /processed/summary.txt",
                    "model": MODEL,
                    "tools": ["file_write"],
                },
            ],
            "edges": [{"from": "extract", "to": "summarize"}],
        }
    )

    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "c1",
                        "file_write",
                        {
                            "path": "/processed/summary.txt",
                            "content": "Invoice from Acme Corp, $1,234.56",
                        },
                    )
                ]
            ),
            text_response("Filed."),
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

    instance_holder: dict[str, WorkflowInstance] = {}
    completed = asyncio.Event()

    async def on_event(payload: dict[str, Any]) -> None:
        instance = await engine.run(definition, trigger_payload=payload)
        instance_holder["instance"] = instance
        completed.set()

    trigger = FilesystemTrigger(folder=tmp_path, pattern="*.pdf")
    await trigger.start(on_event)
    try:
        target = tmp_path / "invoice-001.pdf"
        _make_pdf(target, "Invoice from Acme Corp, $1,234.56")
        await asyncio.wait_for(completed.wait(), timeout=10.0)
    finally:
        await trigger.stop()

    instance = instance_holder["instance"]

    # Workflow ran to completion.
    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error

    # Results landed in the mock world.
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files["/processed/summary.txt"] == b"Invoice from Acme Corp, $1,234.56"

    # The deterministic extract step actually read the dropped file.
    extract_output = instance.context["steps"]["extract"]
    assert "Acme" in extract_output["text"]
    assert extract_output["is_native"] is True

    # Audit log contains every action.
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert actions[0] == "workflow_started"
    assert actions[-1] == "workflow_completed"
    assert actions.count("step_started") == 2
    assert actions.count("step_completed") == 2
    tool_calls = [e for e in audit if e.action == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].detail["name"] == "file_write"
    assert tool_calls[0].detail["input"]["path"] == "/processed/summary.txt"


# --- Criterion 2: deterministic replay in <1s ---


async def test_criterion_2_deterministic_replay_under_one_second() -> None:
    """A workflow with one deterministic step plus one agentic step (against
    fake Bedrock) runs to completion in well under a second."""
    repos = in_memory_repositories()
    fns = FunctionRegistry()

    async def trivial(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"value": 1}

    fns.register("trivial", trivial)

    definition = load_definition(
        {
            "id": "deterministic-replay",
            "name": "deterministic-replay",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "trivial"},
                {
                    "id": "b",
                    "type": "agentic",
                    "goal": "acknowledge",
                    "model": MODEL,
                    "tools": [],
                },
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
    )
    bedrock = FakeBedrock([text_response("ok", input_tokens=5, output_tokens=2)])
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )

    start = time.perf_counter()
    instance = await engine.run(definition)
    elapsed = time.perf_counter() - start

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert elapsed < 1.0, f"Replay took {elapsed:.3f}s, must be <1s"


# --- Criterion 3: a new tool can be added trivially ---


class _UppercaseTool(Tool):
    """Toy tool added in-line to demonstrate how little is needed to extend
    the system. Real new tools follow the same shape and live in
    `workflow_platform/tools/`.
    """

    name: ClassVar[str] = "uppercase"
    description: ClassVar[str] = "Uppercase a string."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        text = params.get("text")
        if not isinstance(text, str):
            return ToolResult(error="text is required")
        return ToolResult(content={"text": text.upper()})


def test_criterion_3_new_tool_in_a_few_lines() -> None:
    """The contract for adding a new tool is: subclass `Tool`, set three
    `ClassVar`s, implement async `execute`. The whole `_UppercaseTool` above
    is the demonstration. Verify it integrates with the existing primitives."""

    # The Tool renders as a Bedrock toolSpec without any extra wiring.
    spec = _UppercaseTool().to_bedrock_tool_spec()
    assert spec["toolSpec"]["name"] == "uppercase"

    # The Tool plugs into a ToolCatalog the engine accepts.
    catalog = ToolCatalog([_UppercaseTool()])
    assert catalog.get("uppercase") is not None


@pytest.mark.parametrize("text", ["hello", "world"])
async def test_criterion_3_new_tool_works_unmodified_with_engine(text: str) -> None:
    """Same demonstration end-to-end: the toy tool runs through the full
    Agent + Engine path with no engine-side changes."""
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            tool_use_response(tool_uses=[("c1", "uppercase", {"text": text})]),
            text_response(f"Uppercased: {text.upper()}"),
        ]
    )
    definition = load_definition(
        {
            "id": "uppercase-test",
            "name": "uppercase-test",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": f"Uppercase {text!r}",
                    "model": MODEL,
                    "tools": ["uppercase"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([_UppercaseTool()]),
        bedrock=bedrock,
        world=mock_world(),
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED
    audit = await repos.audit.list_by_instance(instance.id)
    tool_call = next(e for e in audit if e.action == "tool_call")
    assert tool_call.detail["result"]["content"]["text"] == text.upper()
