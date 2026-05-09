"""End-to-end capability enforcement: Agent denies disallowed tools, FileRead /
FileWrite reject out-of-scope paths, and the engine threads policy correctly
through system → workflow → step layers.
"""

from __future__ import annotations

from typing import Any

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.security import CapabilityPolicy
from workflow_platform.tools import FileReadTool, FileWriteTool, ToolContext
from workflow_platform.workflow import load_definition
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


# --- tool-allowlist denial ---


async def test_agent_denies_tool_outside_allowlist() -> None:
    """When the engine resolves a step's capability stack to forbid a tool,
    Agent's dispatch returns a capability-denied tool result and the model is
    told the tool failed (so it can choose a different approach)."""
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            tool_use_response(tool_uses=[("c1", "file_write", {"path": "/x.txt", "content": "y"})]),
            text_response("Couldn't write."),
        ]
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
                    "goal": "Try to write",
                    "model": MODEL,
                    "tools": ["file_write"],
                    "capabilities": {"tools": ["file_read"]},  # denies file_write
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    audit = await repos.audit.list_by_instance(instance.id)
    tool_call = next(e for e in audit if e.action == "tool_call")
    error = tool_call.detail["result"]["error"]
    assert error is not None
    assert "Capability denied" in error
    assert "file_write" in error


async def test_system_capability_overrides_step_request() -> None:
    """Even if a step asks for a tool, a more restrictive system layer denies."""
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            tool_use_response(tool_uses=[("c1", "file_write", {"path": "/x.txt", "content": "y"})]),
            text_response("done"),
        ]
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
                    "goal": "Try",
                    "model": MODEL,
                    "tools": ["file_write"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=bedrock,
        world=mock_world(),
        system_capabilities=CapabilityPolicy(tools=["file_read"]),
    )
    instance = await engine.run(definition)
    audit = await repos.audit.list_by_instance(instance.id)
    tool_call = next(e for e in audit if e.action == "tool_call")
    assert "Capability denied" in tool_call.detail["result"]["error"]


# --- file ACLs ---


async def test_file_read_rejects_path_outside_acl() -> None:
    world = mock_world(files={"/secret.txt": b"keep out"})
    ctx = ToolContext(
        world=world,
        capabilities=__resolved(CapabilityPolicy(file_read=["/inbox/*"])),
    )
    result = await FileReadTool().execute({"path": "/secret.txt"}, context=ctx)
    assert not result.ok
    assert result.error is not None
    assert "Capability denied" in result.error


async def test_file_read_within_acl_succeeds() -> None:
    world = mock_world(files={"/inbox/a.txt": b"hi"})
    ctx = ToolContext(
        world=world,
        capabilities=__resolved(CapabilityPolicy(file_read=["/inbox/*"])),
    )
    result = await FileReadTool().execute({"path": "/inbox/a.txt"}, context=ctx)
    assert result.ok
    assert result.content["text"] == "hi"


async def test_file_write_rejects_path_outside_acl() -> None:
    world = mock_world()
    ctx = ToolContext(
        world=world,
        capabilities=__resolved(CapabilityPolicy(file_write=["/processed/*"])),
    )
    result = await FileWriteTool().execute({"path": "/etc/passwd", "content": "x"}, context=ctx)
    assert not result.ok
    assert result.error is not None
    assert "Capability denied" in result.error
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert "/etc/passwd" not in fs.files


async def test_file_write_within_acl_succeeds() -> None:
    world = mock_world()
    ctx = ToolContext(
        world=world,
        capabilities=__resolved(CapabilityPolicy(file_write=["/processed/*"])),
    )
    result = await FileWriteTool().execute(
        {"path": "/processed/out.txt", "content": "x"}, context=ctx
    )
    assert result.ok


# --- helpers ---


def __resolved(*layers: CapabilityPolicy) -> Any:
    """Resolve directly to a ResolvedCapabilities for tool-level tests."""
    from workflow_platform.security import resolve_capabilities

    return resolve_capabilities(*layers)
