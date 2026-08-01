"""Raw-trace vault dark dual-write (docs/TRACE_GOVERNANCE_PLAN.md §4.1, TG3a):
the engine copies raw (trigger payload, tool calls, model output) into the
vault keyed on the immutable step-attempt, while the operational store keeps
its inline copy authoritative. Plus the projector + collision-free key."""

from __future__ import annotations

from typing import Any, ClassVar

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import RawTraceKind, in_memory_repositories
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.trace_vault import idempotency_key, raw_kinds_of_output
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET_IN = "VAULT-SENTINEL-IN"
SECRET_OUT = "VAULT-SENTINEL-OUT"
TRIGGER_BODY = "VAULT-TRIGGER-BODY"


class _SecretTool(Tool):
    name = "leaky_tool"
    description = "returns sensitive content"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content={"text": SECRET_OUT})


def _engine() -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog([_SecretTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("t1", "leaky_tool", {"body": SECRET_IN})]),
                text_response(f"Saw {SECRET_OUT}"),
            ]
        ),
        world=mock_world(),
    )


def _definition() -> Any:
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


# --- projector + key (pure) ---


def test_projector_vaults_raw_leaves_structured() -> None:
    kinds = raw_kinds_of_output(
        {
            "tool_calls": [{"name": "t"}],
            "output_text": "free text",
            "recall": "prior thread",
            "category": "urgent",  # structured/safe — NOT vaulted
            "cost_usd": 0.01,
        }
    )
    assert set(kinds) == {
        RawTraceKind.TOOL_CALLS,
        RawTraceKind.MODEL_OUTPUT,
        RawTraceKind.RECALL,
    }
    # empty raw fields are skipped
    assert raw_kinds_of_output({"tool_calls": [], "output_text": ""}) == {}


def test_idempotency_key_is_collision_free() -> None:
    k = idempotency_key
    # two different step attempts (same attempt NUMBER, different rows) differ
    assert k("o", "i", "attempt-A", RawTraceKind.MODEL_OUTPUT) != k(
        "o", "i", "attempt-B", RawTraceKind.MODEL_OUTPUT
    )
    # instance-level (trigger) is a separate space from any step attempt
    assert k("o", "i", None, RawTraceKind.TRIGGER_PAYLOAD) != k(
        "o", "i", "attempt-A", RawTraceKind.TRIGGER_PAYLOAD
    )
    # deterministic — a retry re-addresses the same object
    assert k("o", "i", "a", RawTraceKind.TOOL_CALLS) == k("o", "i", "a", RawTraceKind.TOOL_CALLS)


# --- end-to-end dark dual-write ---


async def test_run_dark_dual_writes_raw_and_keeps_inline() -> None:
    engine = _engine()
    instance = await engine.run(
        _definition(), trigger_payload={"subject": "S", "body": TRIGGER_BODY}
    )
    assert instance.state.value == "completed"

    vault = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    kinds = {v.kind for v in vault}
    assert RawTraceKind.TRIGGER_PAYLOAD in kinds
    assert RawTraceKind.TOOL_CALLS in kinds
    assert RawTraceKind.MODEL_OUTPUT in kinds
    # every vault row is keyed + stamped
    assert all(v.idempotency_key and v.projector_version for v in vault)
    # the trigger row is instance-level; the output rows carry a step-attempt id
    trigger_rows = [v for v in vault if v.kind is RawTraceKind.TRIGGER_PAYLOAD]
    assert trigger_rows and trigger_rows[0].step_attempt_id is None
    assert all(
        v.step_attempt_id is not None
        for v in vault
        if v.kind in (RawTraceKind.TOOL_CALLS, RawTraceKind.MODEL_OUTPUT)
    )

    # DARK: the operational store is UNCHANGED — inline still holds the raw.
    assert instance.trigger_payload["body"] == TRIGGER_BODY
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    act = next(s for s in steps if s.step_id == "act")
    assert act.output is not None
    assert SECRET_IN in str(act.output["tool_calls"])  # inline raw preserved


async def test_vault_put_is_idempotent() -> None:
    engine = _engine()
    instance = await engine.run(_definition(), trigger_payload={"body": TRIGGER_BODY})
    before = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    # re-vault the same trigger (same idempotency key) → no duplicate row
    await engine._vault.record_trigger(
        org_id=instance.org_id, instance_id=instance.id, payload=instance.trigger_payload
    )
    after = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    assert len(after) == len(before)
