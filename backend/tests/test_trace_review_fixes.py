"""Regression tests for the external code review (2026-08-02). Each reproduces
a bypass the reviewer found and asserts it is now closed. New fixes append
here as they land."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, StepFailure, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import (
    RawTrace,
    RawTraceKind,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.trace_cipher import ENV_MASTER_KEY
from workflow_platform.trace_projection import redact_error, redact_tool_data
from workflow_platform.trace_rehydrate import RawTraceRehydrator, RawTraceUnavailable
from workflow_platform.trace_vault import RawTraceVault, idempotency_key
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET = "REVIEW-FIX-SECRET"
RAW_ERR = "BOOM-RAW-ERROR-SENTINEL"
_KEY = base64.b64encode(b"review-fixes-master-key-32-byte!").decode()


# --- F1: default-deny projector (no-tool output + arbitrary fields) ---


def test_f1_no_tool_output_text_is_redacted() -> None:
    # the reviewer's repro: a step that used NO tool still leaks output_text
    safe = redact_tool_data({"output_text": SECRET, "category": "urgent"}, admin=False)
    assert SECRET not in str(safe["output_text"])
    assert safe["category"] == "urgent"  # validated enum survives


def test_f1_arbitrary_and_nested_fields_are_redacted() -> None:
    safe = redact_tool_data(
        {
            "summary": SECRET,
            "nested": {"reason": SECRET},
            "key_concepts": [SECRET],
            "cost_usd": 0.5,
        },
        admin=False,
    )
    assert SECRET not in str(safe)
    assert safe["cost_usd"] == 0.5  # numeric survives by type


async def test_f1_redacted_field_is_vaulted_and_rehydrated_lossless() -> None:
    # a free-form business field is redacted below-grant AND recoverable
    repos = in_memory_repositories()
    output = {"category": "urgent", "summary": SECRET, "cost_usd": 0.01}
    await RawTraceVault(repos).record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output=output, durable=True
    )
    safe = redact_tool_data(output, admin=False)
    assert safe["category"] == "urgent" and SECRET not in str(safe)
    full = await RawTraceRehydrator(repos).rehydrate_output(
        purpose="resume", org_id="o", instance_id="i", step_attempt_id="s", safe_output=safe
    )
    assert full["summary"] == SECRET  # lossless


# --- F2: error text is a first-class raw kind ---


async def _boom(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    raise StepFailure(RAW_ERR)


def _failing_engine(*, safe_only: bool) -> WorkflowEngine:
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=safe_only,
    )
    engine.functions.register("boom", _boom)
    return engine


def _failing_def() -> Any:
    return load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "s", "type": "deterministic", "function": "boom"}],
            "edges": [],
        }
    )


def test_f2_redact_error_unit() -> None:
    assert redact_error("boom", admin=True) == "boom"
    assert redact_error(None, admin=False) is None
    assert (redact_error("boom", admin=False) or "").startswith("[redacted")


async def test_f2_flip_error_is_marker_at_rest_raw_in_vault() -> None:
    engine = _failing_engine(safe_only=True)
    inst = await engine.run(_failing_def(), trigger_payload={})
    assert inst.state == WorkflowInstanceState.FAILED
    # operational store (step + instance) holds only the marker — no raw at rest
    step = (await engine.repositories.steps.list_by_instance(inst.id))[0]
    assert step.error and RAW_ERR not in step.error
    assert inst.error and RAW_ERR not in inst.error
    # the raw error IS vaulted, and a grant-holder rehydrates it
    rehy = RawTraceRehydrator(engine.repositories)
    assert (
        await rehy.merge_error(
            org_id=inst.org_id, instance_id=inst.id, step_attempt_id=step.id, safe_error=step.error
        )
        == RAW_ERR
    )
    assert (
        await rehy.merge_error(
            org_id=inst.org_id, instance_id=inst.id, step_attempt_id=None, safe_error=inst.error
        )
        == RAW_ERR
    )


async def test_f2_no_flip_keeps_error_inline() -> None:
    engine = _failing_engine(safe_only=False)
    inst = await engine.run(_failing_def(), trigger_payload={})
    step = (await engine.repositories.steps.list_by_instance(inst.id))[0]
    assert RAW_ERR in (step.error or "")  # dark dual-write keeps raw inline


# --- F6: cipher binding at the rehydrator (substitution + downgrade) ---


async def test_f6_row_substitution_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MASTER_KEY, _KEY)
    repos = in_memory_repositories()
    await RawTraceVault(repos).record_step_output(
        org_id="orgA",
        instance_id="i1",
        step_attempt_id="s1",
        output={"tool_calls": [{"name": "t", "input": {"body": SECRET}}]},
    )
    row = (await repos.raw_trace_vault.list_by_instance("i1"))[0]
    r = RawTraceRehydrator(repos)
    # the CORRECT identity opens it
    ok = r._payload_of(row, org_id="orgA", instance_id="i1", step_attempt_id="s1", kind="output")
    assert SECRET in str(ok)
    # the SAME row served under any wrong identity (DB-operator substitution) is rejected
    base = {"org_id": "orgA", "instance_id": "i1", "step_attempt_id": "s1", "kind": "output"}
    for bad in (
        {"org_id": "orgB"},
        {"instance_id": "iX"},
        {"step_attempt_id": "sX"},
        {"kind": "recall"},
    ):
        with pytest.raises(RawTraceUnavailable):
            r._payload_of(row, **{**base, **bad})


async def test_f6_plaintext_downgrade_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MASTER_KEY, _KEY)
    repos = in_memory_repositories()
    # a DB operator inserts a PLAINTEXT row under the expected key
    key = idempotency_key("orgA", "i1", "s1", RawTraceKind.OUTPUT)
    await repos.raw_trace_vault.put(
        RawTrace(
            org_id="orgA",
            instance_id="i1",
            step_attempt_id="s1",
            kind=RawTraceKind.OUTPUT,
            idempotency_key=key,
            payload={"body": SECRET},  # NOT sealed
        )
    )
    row = (await repos.raw_trace_vault.list_by_instance("i1"))[0]
    with pytest.raises(RawTraceUnavailable):  # unsealed under encryption → rejected
        RawTraceRehydrator(repos)._payload_of(
            row, org_id="orgA", instance_id="i1", step_attempt_id="s1", kind="output"
        )


# --- F7: projected trigger with a missing vault row must fail closed ---


async def test_f7_projected_trigger_missing_vault_fails_closed() -> None:
    r = RawTraceRehydrator(in_memory_repositories())
    with pytest.raises(RawTraceUnavailable):
        await r.rehydrate_trigger(
            purpose="resume",
            org_id="o",
            instance_id="i",
            safe_trigger={"_redacted": "routing only"},  # marker → raw was projected away
        )


async def test_f7_unprojected_trigger_with_no_row_is_fine() -> None:
    # a trigger with no sensitive content was never projected → no vault row needed
    r = RawTraceRehydrator(in_memory_repositories())
    out = await r.rehydrate_trigger(
        purpose="resume", org_id="o", instance_id="i", safe_trigger={"message_id": "m1"}
    )
    assert out == {"message_id": "m1"}
