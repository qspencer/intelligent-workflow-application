"""Vault rehydration read-back + system-access audit
(docs/TRACE_GOVERNANCE_PLAN.md §4.3/§3.2, TG3b). Proves the vault is a
faithful read-back source (the flip's precondition): a PROJECTED safe output
rehydrates to the original raw from the vault, audited before fetch and
fail-closed on a missing row or an unrecordable access."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.api.redaction import redact_tool_data
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.trace_projection import PROJECTOR_VERSION
from workflow_platform.trace_rehydrate import RawTraceRehydrator, RawTraceUnavailable
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET_IN = "REHYDRATE-IN"
SECRET_OUT = "REHYDRATE-OUT"


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


_DEF = {
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


async def _run() -> tuple[WorkflowEngine, Any, Any]:
    engine = _engine()
    instance = await engine.run(load_definition(_DEF), trigger_payload={"body": "TRIG"})
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    act = next(s for s in steps if s.step_id == "act")
    return engine, instance, act


async def test_projected_output_rehydrates_to_raw() -> None:
    engine, instance, act = await _run()
    raw_output = act.output
    # what the flip would persist to the operational store:
    safe = redact_tool_data(raw_output, admin=False)
    assert SECRET_IN not in str(safe) and SECRET_OUT not in str(safe)  # projected

    rehydrated = await RawTraceRehydrator(engine.repositories).rehydrate_output(
        purpose="resume",
        org_id=instance.org_id,
        instance_id=instance.id,
        step_attempt_id=act.id,
        safe_output=safe,
        projector_version=PROJECTOR_VERSION,
    )
    # raw restored from the vault, matching the original inline output
    assert rehydrated["tool_calls"] == raw_output["tool_calls"]
    assert rehydrated["output_text"] == raw_output["output_text"]
    assert SECRET_IN in str(rehydrated) and SECRET_OUT in str(rehydrated)

    audit = await engine.repositories.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "raw_trace_system_access_attempted" in actions
    completed = [e for e in audit if e.action == "raw_trace_system_access_completed"]
    assert completed and completed[-1].detail["outcome"] == "succeeded"


async def test_missing_vault_row_fails_closed() -> None:
    engine, instance, act = await _run()
    safe = redact_tool_data(act.output, admin=False)
    with pytest.raises(RawTraceUnavailable):
        await RawTraceRehydrator(engine.repositories).rehydrate_output(
            purpose="resume",
            org_id=instance.org_id,
            instance_id=instance.id,
            step_attempt_id="no-such-attempt",  # no vault row for this attempt
            safe_output=safe,
            projector_version=PROJECTOR_VERSION,
        )
    outcomes = [
        e.detail.get("outcome")
        for e in await engine.repositories.audit.list_by_instance(instance.id)
        if e.action == "raw_trace_system_access_completed"
    ]
    assert "retrieval_failed" in outcomes


async def test_unrecordable_access_fails_closed_before_fetch() -> None:
    engine, instance, act = await _run()
    safe = redact_tool_data(act.output, admin=False)

    fetched: list[str] = []

    class _AuditDown:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def append(self, entry: Any) -> Any:
            if entry.action == "raw_trace_system_access_attempted":
                raise RuntimeError("audit down")
            return await self._inner.append(entry)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _VaultSpy:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def get_by_idempotency_key(self, key: str) -> Any:
            fetched.append(key)
            return await self._inner.get_by_idempotency_key(key)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    engine.repositories.audit = _AuditDown(engine.repositories.audit)  # type: ignore[assignment]
    engine.repositories.raw_trace_vault = _VaultSpy(  # type: ignore[assignment]
        engine.repositories.raw_trace_vault
    )

    with pytest.raises(RawTraceUnavailable):
        await RawTraceRehydrator(engine.repositories).rehydrate_output(
            purpose="resume",
            org_id=instance.org_id,
            instance_id=instance.id,
            step_attempt_id=act.id,
            safe_output=safe,
            projector_version=PROJECTOR_VERSION,
        )
    # the attempt audit failed → NO vault fetch happened (fail-closed before fetch)
    assert fetched == []


async def test_trigger_rehydrates_from_vault() -> None:
    engine, instance, _ = await _run()
    full = await RawTraceRehydrator(engine.repositories).rehydrate_trigger(
        purpose="resume",
        org_id=instance.org_id,
        instance_id=instance.id,
        safe_trigger={"_redacted": "routing only"},
    )
    assert full["body"] == "TRIG"  # raw trigger restored from the vault
