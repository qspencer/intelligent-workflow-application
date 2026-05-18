"""Tests for the TriggerOrchestrator — startup wiring of file/schedule/webhook
triggers against the running engine."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.orchestrator import TriggerOrchestrator
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.triggers import WebhookRegistry
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _make_engine(bedrock: FakeBedrock | None = None) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock or FakeBedrock([]),
        world=mock_world(),
    )


def _write_yaml(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    return path


def _orchestrator(
    dir_path: Path, *, engine: WorkflowEngine, registry: WebhookRegistry | None = None
) -> TriggerOrchestrator:
    return TriggerOrchestrator(
        definitions_dir=dir_path,
        repositories=engine.repositories,
        engine=engine,
        webhook_registry=registry or WebhookRegistry(),
    )


# --- empty / missing directory ---


async def test_missing_directory_logs_and_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    orch = _orchestrator(tmp_path / "does-not-exist", engine=_make_engine())
    caplog.set_level(logging.WARNING)
    await orch.start()
    assert "does not exist" in caplog.text


async def test_empty_directory_logs_and_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    orch = _orchestrator(tmp_path, engine=_make_engine())
    caplog.set_level(logging.WARNING)
    await orch.start()
    assert "No *.yaml" in caplog.text


# --- trigger registration by type ---


async def test_manual_trigger_does_not_start_anything(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "manual.yaml",
        """\
id: manual-wf
name: Manual WF
trigger: {type: manual}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    orch = _orchestrator(tmp_path, engine=_make_engine())
    await orch.start()
    assert orch._started == []


async def test_webhook_trigger_registers_in_registry(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "wh.yaml",
        """\
id: wh-wf
name: WH
trigger: {type: webhook, config: {trigger_id: hook-1}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    registry = WebhookRegistry()
    orch = _orchestrator(tmp_path, engine=_make_engine(), registry=registry)
    await orch.start()

    assert registry.is_registered("hook-1")
    await orch.stop()
    assert not registry.is_registered("hook-1")


async def test_webhook_fire_runs_engine(tmp_path: Path) -> None:
    """End-to-end: a webhook fire should walk the engine.run path and produce
    a workflow instance in the repos. Uses FakeBedrock with no responses
    because the workflow is deterministic-only."""
    _write_yaml(
        tmp_path,
        "wh.yaml",
        """\
id: wh-wf
name: WH
trigger: {type: webhook, config: {trigger_id: hook-2}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    engine = _make_engine()
    registry = WebhookRegistry()
    orch = _orchestrator(tmp_path, engine=engine, registry=registry)
    await orch.start()

    fired = await registry.fire("hook-2", {"from": "test"})
    assert fired is True

    instances = await engine.repositories.instances.list_recent(limit=5)
    assert len(instances) == 1
    assert instances[0].workflow_id == "wh-wf"
    assert instances[0].trigger_payload == {"from": "test"}

    await orch.stop()


async def test_schedule_trigger_fires_engine_on_short_interval(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "sched.yaml",
        """\
id: sched-wf
name: Sched
trigger: {type: schedule, config: {interval_seconds: 0.1}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    engine = _make_engine()
    orch = _orchestrator(tmp_path, engine=engine)
    await orch.start()
    # Give the schedule loop two ticks to fire.
    await asyncio.sleep(0.35)
    await orch.stop()

    instances = await engine.repositories.instances.list_recent(limit=10)
    assert len(instances) >= 1
    assert all(i.workflow_id == "sched-wf" for i in instances)


async def test_filesystem_trigger_fires_engine_on_file_drop(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    yaml_dir = tmp_path / "defs"
    yaml_dir.mkdir()
    _write_yaml(
        yaml_dir,
        "fs.yaml",
        f"""\
id: fs-wf
name: FS
trigger:
  type: filesystem
  config: {{path: {inbox}, pattern: '*.txt'}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    engine = _make_engine()
    orch = _orchestrator(yaml_dir, engine=engine)
    await orch.start()

    # Drop a file. watchdog runs in a separate thread, so allow some slack.
    (inbox / "drop.txt").write_text("hi")
    for _ in range(20):
        await asyncio.sleep(0.1)
        if await engine.repositories.instances.list_recent(limit=1):
            break

    instances = await engine.repositories.instances.list_recent(limit=5)
    await orch.stop()

    assert len(instances) >= 1
    assert instances[0].workflow_id == "fs-wf"
    assert "drop.txt" in instances[0].trigger_payload["file_path"]


# --- robustness ---


async def test_unknown_trigger_type_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_yaml(
        tmp_path,
        "weird.yaml",
        """\
id: weird-wf
name: Weird
trigger: {type: smoke_signals}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    orch = _orchestrator(tmp_path, engine=_make_engine())
    caplog.set_level(logging.WARNING)
    await orch.start()
    assert orch._started == []
    assert "smoke_signals" in caplog.text


async def test_malformed_yaml_is_skipped_others_still_register(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_yaml(tmp_path, "broken.yaml", "this: [is: not: valid")
    _write_yaml(
        tmp_path,
        "good.yaml",
        """\
id: good-wf
name: Good
trigger: {type: webhook, config: {trigger_id: g1}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    registry = WebhookRegistry()
    orch = _orchestrator(tmp_path, engine=_make_engine(), registry=registry)
    caplog.set_level(logging.ERROR)
    await orch.start()

    assert registry.is_registered("g1")
    assert "broken.yaml" in caplog.text
    await orch.stop()


async def test_callback_exception_does_not_crash_orchestrator(tmp_path: Path) -> None:
    """A bug below engine.run (e.g. repository write failure) should be logged
    and absorbed; subsequent events still fire."""
    _write_yaml(
        tmp_path,
        "wh.yaml",
        """\
id: bug-wf
name: Bug
trigger: {type: webhook, config: {trigger_id: bug-hook}}
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
""",
    )
    engine = _make_engine()
    registry = WebhookRegistry()
    orch = _orchestrator(tmp_path, engine=engine, registry=registry)
    await orch.start()

    # Sabotage repos.instances.create just for one fire.
    from workflow_platform.persistence import WorkflowInstance

    original = engine.repositories.instances.create
    calls = {"n": 0}

    async def boom(instance: WorkflowInstance) -> WorkflowInstance:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated DB failure")
        return await original(instance)

    engine.repositories.instances.create = boom  # type: ignore[method-assign]

    await registry.fire("bug-hook", {})  # this one explodes
    engine.repositories.instances.create = original  # type: ignore[method-assign]
    await registry.fire("bug-hook", {})  # this one should succeed

    instances = await engine.repositories.instances.list_recent(limit=5)
    assert len(instances) == 1
    await orch.stop()


# --- agentic workflow via webhook end-to-end ---


# --- agent_memory.md auto-seeding (G6) ---


async def test_seed_memory_writes_per_agentic_step(tmp_path: Path) -> None:
    from workflow_platform.memory import MemoryManager
    from workflow_platform.orchestrator import seed_memory_from_workflow_dir

    yaml_path = _write_yaml(
        tmp_path,
        "wf.yaml",
        """\
id: seed-wf
name: Seed WF
trigger: {type: manual}
steps:
  - id: a
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: []
    goal: do
  - id: b
    type: deterministic
    function: noop
  - id: c
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: []
    goal: again
edges: []
""",
    )
    (tmp_path / "agent_memory.md").write_text("# Rubric\nfollow this")
    memory_dir = tmp_path / "_mem"
    memory = MemoryManager(memory_dir)

    from workflow_platform.workflow import load_definition_from_file

    definition = load_definition_from_file(yaml_path)
    seeded = await seed_memory_from_workflow_dir(definition, yaml_path, memory)

    assert seeded is True
    # Both agentic steps got the rubric; the deterministic step did not.
    assert (memory_dir / "steps" / "seed-wf" / "a.md").read_text() == "# Rubric\nfollow this"
    assert (memory_dir / "steps" / "seed-wf" / "c.md").read_text() == "# Rubric\nfollow this"
    assert not (memory_dir / "steps" / "seed-wf" / "b.md").exists()


async def test_seed_memory_is_noop_without_file(tmp_path: Path) -> None:
    from workflow_platform.memory import MemoryManager
    from workflow_platform.orchestrator import seed_memory_from_workflow_dir
    from workflow_platform.workflow import load_definition_from_file

    yaml_path = _write_yaml(
        tmp_path,
        "wf.yaml",
        """\
id: no-mem-wf
name: No Mem
trigger: {type: manual}
steps:
  - id: a
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: []
    goal: do
edges: []
""",
    )
    memory = MemoryManager(tmp_path / "_mem")
    definition = load_definition_from_file(yaml_path)
    assert await seed_memory_from_workflow_dir(definition, yaml_path, memory) is False
    assert not (tmp_path / "_mem").exists() or not list((tmp_path / "_mem").rglob("*.md"))


async def test_seed_memory_is_noop_when_memory_is_none(tmp_path: Path) -> None:
    from workflow_platform.orchestrator import seed_memory_from_workflow_dir
    from workflow_platform.workflow import load_definition_from_file

    yaml_path = _write_yaml(
        tmp_path,
        "wf.yaml",
        """\
id: no-mm-wf
name: No MM
trigger: {type: manual}
steps:
  - id: a
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: []
    goal: do
edges: []
""",
    )
    (tmp_path / "agent_memory.md").write_text("ignored")
    definition = load_definition_from_file(yaml_path)
    assert await seed_memory_from_workflow_dir(definition, yaml_path, None) is False


async def test_orchestrator_start_seeds_memory_for_workflows_with_adjacent_md(
    tmp_path: Path,
) -> None:
    """End-to-end: orchestrator.start finds adjacent agent_memory.md, seeds
    the engine's MemoryManager, and the agent step's audit output has a
    non-null memory_hash on the next run."""
    from workflow_platform.memory import MemoryManager

    defs_dir = tmp_path / "defs"
    defs_dir.mkdir()
    _write_yaml(
        defs_dir,
        "wf.yaml",
        """\
id: mem-orch-wf
name: Mem Orch
trigger: {type: webhook, config: {trigger_id: mem-hook}}
steps:
  - id: act
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: []
    goal: go
edges: []
""",
    )
    (defs_dir / "agent_memory.md").write_text("# Rubric\nbe concise")

    memory = MemoryManager(tmp_path / "_mem")
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([text_response("ok")]),
        world=mock_world(),
        memory=memory,
    )
    registry = WebhookRegistry()
    orch = TriggerOrchestrator(
        definitions_dir=defs_dir,
        repositories=engine.repositories,
        engine=engine,
        webhook_registry=registry,
    )
    await orch.start()
    await registry.fire("mem-hook", {"src": "test"})
    await orch.stop()

    steps = await repos.steps.list_by_instance((await repos.instances.list_recent(limit=1))[0].id)
    assert steps[0].output is not None
    memory_hash = steps[0].output["memory_hash"]
    assert isinstance(memory_hash, str)
    assert memory_hash.startswith("sha256:")


async def test_orchestrator_routes_agentic_workflow_via_webhook(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "agentic.yaml",
        f"""\
id: agentic-wf
name: Agentic
trigger: {{type: webhook, config: {{trigger_id: agentic-hook}}}}
steps:
  - id: act
    type: agentic
    model: {MODEL}
    tools: []
    goal: Say hi.
    policy: {{max_iterations: 1, max_total_tokens: 1000}}
edges: []
""",
    )
    bedrock = FakeBedrock([text_response("hi", input_tokens=5, output_tokens=2)])
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    registry = WebhookRegistry()
    orch = _orchestrator(tmp_path, engine=engine, registry=registry)
    await orch.start()
    await registry.fire("agentic-hook", {"src": "test"})
    await orch.stop()

    instances = await engine.repositories.instances.list_recent(limit=5)
    assert len(instances) == 1
    assert instances[0].state.value == "completed"
    steps = await engine.repositories.steps.list_by_instance(instances[0].id)
    assert steps[0].output is not None
    assert steps[0].output["output_text"] == "hi"
