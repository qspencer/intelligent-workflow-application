"""The safe-only flip (docs/TRACE_GOVERNANCE_PLAN.md §4, TG3b part 2): with
`trace_safe_only` on, the operational store persists ONLY the safe projection
and raw lives in the vault; execution stays correct because resume/fork
rehydrate from the vault. Default OFF keeps today's dark dual-write."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import (
    RawTraceKind,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET_IN = "FLIP-IN"
SECRET_OUT = "FLIP-OUT"
TRIG = "FLIP-TRIGGER-BODY"


class _SecretTool(Tool):
    name = "leaky_tool"
    description = "returns sensitive content"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content={"text": SECRET_OUT})


def _bedrock(n: int = 1) -> FakeBedrock:
    frames: list[Any] = []
    for _ in range(n):
        frames.append(tool_use_response(tool_uses=[("t1", "leaky_tool", {"body": SECRET_IN})]))
        frames.append(text_response(f"Saw {SECRET_OUT}"))
    return FakeBedrock(frames)


def _engine(*, safe_only: bool, bedrock: FakeBedrock | None = None) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog([_SecretTool()]),
        bedrock=bedrock or _bedrock(),
        world=mock_world(),
        trace_safe_only=safe_only,
    )


def _one_step() -> Any:
    return load_definition(
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


async def _noop(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    return {"ok": True}


async def test_flip_operational_store_is_zero_raw_vault_has_raw() -> None:
    engine = _engine(safe_only=True)
    instance = await engine.run(_one_step(), trigger_payload={"subject": "S", "body": TRIG})
    assert instance.state == WorkflowInstanceState.COMPLETED

    # Operational store: NO raw anywhere the reviewer's criterion 4 names.
    persisted = await engine.repositories.instances.get(instance.id)
    assert persisted is not None
    op_blob = str(persisted.model_dump())
    for secret in (SECRET_IN, SECRET_OUT, TRIG):
        assert secret not in op_blob, f"{secret} leaked into instance (trigger/context)"
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    act = next(s for s in steps if s.step_id == "act")
    assert SECRET_IN not in str(act.output) and SECRET_OUT not in str(act.output)
    # routing survives the trigger projection
    assert persisted.trigger_payload.get("_redacted")
    # the step_completed audit is projected at rest too
    step_done = [
        e
        for e in await engine.repositories.audit.list_by_instance(instance.id)
        if e.action == "step_completed"
    ]
    assert step_done and SECRET_OUT not in str(step_done[0].detail)

    # The vault DOES have the raw.
    vault = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    vblob = str([v.payload for v in vault])
    assert SECRET_IN in vblob and SECRET_OUT in vblob and TRIG in vblob
    assert {RawTraceKind.TRIGGER_PAYLOAD, RawTraceKind.TOOL_CALLS, RawTraceKind.MODEL_OUTPUT} <= {
        v.kind for v in vault
    }


async def test_flip_projects_tool_call_audit_at_rest() -> None:
    engine = _engine(safe_only=True)
    instance = await engine.run(_one_step(), trigger_payload={"body": TRIG})
    audit = await engine.repositories.audit.list_by_instance(instance.id)
    tcs = [e for e in audit if e.action == "tool_call"]
    assert tcs  # the leaky tool was called
    for e in tcs:
        assert SECRET_IN not in str(e.detail)  # raw input projected out at rest
        assert e.detail.get("_redacted")  # safe_tool_call metadata marker
        assert e.detail.get("input_keys") == ["body"]  # keys survive, values don't


async def test_default_off_keeps_inline_raw() -> None:
    engine = _engine(safe_only=False)
    instance = await engine.run(_one_step(), trigger_payload={"body": TRIG})
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    act = next(s for s in steps if s.step_id == "act")
    # dark dual-write: inline still authoritative
    assert act.output is not None
    assert SECRET_IN in str(act.output["tool_calls"])
    assert instance.trigger_payload["body"] == TRIG


async def test_durable_or_fail_flip_run_fails_when_vault_write_fails() -> None:
    engine = _engine(safe_only=True)

    class _VaultDown:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def put(self, trace: Any) -> Any:
            raise RuntimeError("vault down")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    engine.repositories.raw_trace_vault = _VaultDown(  # type: ignore[assignment]
        engine.repositories.raw_trace_vault
    )
    # a lost raw write under the flip must not silently drop raw — it raises
    with pytest.raises(RuntimeError):
        await engine.run(_one_step(), trigger_payload={"body": TRIG})


async def test_fork_rehydrates_and_rebinds_raw_under_flip() -> None:
    engine = _engine(safe_only=True, bedrock=_bedrock(n=2))  # source run + fork re-run
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
                },
                {"id": "route", "type": "deterministic", "function": "noop"},
            ],
            "edges": [{"from": "act", "to": "route"}],
        }
    )
    engine.functions.register("noop", _noop)
    source = await engine.run(definition, trigger_payload={"body": TRIG})
    assert source.state == WorkflowInstanceState.COMPLETED

    # Fork from `route` → `act` is preserved. The fork must rehydrate act's raw
    # from the SOURCE vault and RE-BIND its own copy (so deleting the source
    # can't break it, §4.3).
    fork = await engine.fork(definition, source.id, "route")
    assert fork.state == WorkflowInstanceState.COMPLETED
    fork_vault = str(
        [v.payload for v in await engine.repositories.raw_trace_vault.list_by_instance(fork.id)]
    )
    assert SECRET_IN in fork_vault  # rehydrated from source + re-bound under the fork


async def test_resume_rehydrates_from_vault_under_flip() -> None:
    engine = _engine(safe_only=True)
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "policies": {"max_total_tokens": 1, "budget_action": "pause"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "call the tool",
                    "model": "claude-haiku-4-5",
                    "tools": ["leaky_tool"],
                },
                {"id": "route", "type": "deterministic", "function": "noop"},
            ],
            "edges": [{"from": "act", "to": "route"}],
        }
    )
    engine.functions.register("noop", _noop)
    paused = await engine.run(definition, trigger_payload={"body": TRIG})
    assert paused.state == WorkflowInstanceState.PAUSED  # budget paused after act

    # Resume rebuilds context from the projected operational store, then
    # rehydrates act's raw from the vault — the run completes.
    resumed = await engine.resume(definition, paused.id)
    assert resumed.state == WorkflowInstanceState.COMPLETED
