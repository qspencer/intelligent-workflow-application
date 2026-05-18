"""Tests for the webhook-echo and scheduled-health-report example workflows.

Confirms:
- The YAML parses to a valid `WorkflowDefinition`.
- The workflow runs end-to-end against `FakeBedrock` + an isolated `World`.
- The expected outputs land in step output / world filesystem.
- The `append_file` stock function appends correctly across multiple calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.functions import append_file
from workflow_platform.engine.registry import StepFailure
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import World, mock_world

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent / "examples"


# --- append_file stock function ---


async def test_append_file_creates_then_appends() -> None:
    world = mock_world()
    ctx = WorkflowContext(instance_id="i", workflow_id="w", steps={"s": {"output_text": "hello"}})
    result = await append_file(
        {"content_from": "steps.s.output_text", "path": "/log/out.log"}, ctx, world
    )
    assert result["path"] == "/log/out.log"
    assert result["appended_chars"] == 5
    assert await world.fs.read_bytes("/log/out.log") == b"hello\n"

    ctx2 = WorkflowContext(instance_id="i", workflow_id="w", steps={"s": {"output_text": "world"}})
    await append_file({"content_from": "steps.s.output_text", "path": "/log/out.log"}, ctx2, world)
    assert await world.fs.read_bytes("/log/out.log") == b"hello\nworld\n"


async def test_append_file_inserts_separator_when_existing_has_no_trailing_newline() -> None:
    world = mock_world()
    await world.fs.write_bytes("/log/a.log", b"no-newline-here")
    ctx = WorkflowContext(
        instance_id="i", workflow_id="w", steps={"s": {"output_text": "addition"}}
    )
    await append_file({"content_from": "steps.s.output_text", "path": "/log/a.log"}, ctx, world)
    assert await world.fs.read_bytes("/log/a.log") == b"no-newline-here\naddition\n"


async def test_append_file_requires_content_from() -> None:
    world = mock_world()
    ctx = WorkflowContext(instance_id="i", workflow_id="w")
    with pytest.raises(StepFailure):
        await append_file({"path": "/log/x.log"}, ctx, world)


async def test_append_file_requires_path() -> None:
    world = mock_world()
    ctx = WorkflowContext(instance_id="i", workflow_id="w", steps={"s": {"output_text": "content"}})
    with pytest.raises(StepFailure):
        await append_file({"content_from": "steps.s.output_text"}, ctx, world)


# --- example: webhook_echo ---


def test_webhook_echo_yaml_parses() -> None:
    yaml_path = EXAMPLES_ROOT / "webhook_echo" / "workflow.yaml"
    definition = load_definition_from_yaml(yaml_path.read_text())
    assert definition.id == "webhook-echo"
    assert definition.trigger.type == "webhook"
    assert definition.trigger.config["trigger_id"] == "echo"
    assert [s.id for s in definition.steps] == ["summarize"]


@pytest.mark.asyncio
async def test_webhook_echo_runs_end_to_end() -> None:
    yaml_path = EXAMPLES_ROOT / "webhook_echo" / "workflow.yaml"
    definition = load_definition_from_yaml(yaml_path.read_text())
    bedrock = FakeBedrock(
        [
            text_response(
                "Alpha's build finished in 12.7 seconds.", input_tokens=50, output_tokens=12
            )
        ]
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    payload: dict[str, Any] = {"event": "build_completed", "project": "alpha", "duration_s": 12.7}
    instance = await engine.run(definition, trigger_payload=payload)

    assert instance.state == WorkflowInstanceState.COMPLETED
    steps = await repos.steps.list_by_instance(instance.id)
    assert len(steps) == 1
    assert steps[0].step_id == "summarize"
    assert steps[0].output is not None
    assert steps[0].output["output_text"] == "Alpha's build finished in 12.7 seconds."


# --- example: scheduled_health_report ---


def test_scheduled_health_report_yaml_parses() -> None:
    yaml_path = EXAMPLES_ROOT / "scheduled_health_report" / "workflow.yaml"
    definition = load_definition_from_yaml(yaml_path.read_text())
    assert definition.id == "scheduled-health-report"
    assert definition.trigger.type == "schedule"
    assert definition.trigger.config["interval_seconds"] == 60
    step_ids = [s.id for s in definition.steps]
    assert step_ids == ["status", "append"]


@pytest.mark.asyncio
async def test_scheduled_health_report_runs_end_to_end(tmp_path: Path) -> None:
    yaml_path = EXAMPLES_ROOT / "scheduled_health_report" / "workflow.yaml"
    # Redirect the log path into tmp_path so the test doesn't write to /tmp directly.
    yaml_body = yaml_path.read_text().replace(
        "/tmp/scheduled-health-report.log", str(tmp_path / "health.log")
    )
    definition = load_definition_from_yaml(yaml_body)

    status_line = "[2026-05-10T17:42:00+00:00] system: ok — no alerts pending"
    bedrock = FakeBedrock([text_response(status_line, input_tokens=60, output_tokens=20)])
    repos = in_memory_repositories()
    # The append_file step writes through World.fs — use a MockWorld variant that
    # bridges to the real filesystem so we can assert on tmp_path directly.
    world: World = _real_world_for_tmp()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=world,
    )

    instance = await engine.run(
        definition,
        trigger_payload={"triggered_at": "2026-05-10T17:42:00+00:00", "schedule": "every 60s"},
    )

    assert instance.state == WorkflowInstanceState.COMPLETED
    log_path = tmp_path / "health.log"
    assert log_path.is_file()
    assert log_path.read_text() == status_line + "\n"

    # Fire it again — the file should grow, not get clobbered.
    bedrock2 = FakeBedrock([text_response("[2026-05-10T17:43:00+00:00] system: ok — idle")])
    engine.bedrock = bedrock2
    await engine.run(
        definition,
        trigger_payload={"triggered_at": "2026-05-10T17:43:00+00:00", "schedule": "every 60s"},
    )
    body = log_path.read_text()
    assert body.startswith(status_line + "\n")
    assert body.endswith("[2026-05-10T17:43:00+00:00] system: ok — idle\n")
    assert body.count("\n") == 2


def _real_world_for_tmp() -> World:
    """The example workflow uses an absolute path that the test redirects into
    tmp_path. We want a `World.fs` whose ops go to the real filesystem so the
    assertion can read the file back."""
    from workflow_platform.world import real_world

    return real_world()
