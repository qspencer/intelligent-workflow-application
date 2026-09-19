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
from workflow_platform.trace_projection import PROJECTOR_VERSION, safe_trigger_payload
from workflow_platform.trace_rehydrate import (
    PROJECTION_UNSUPPORTED,
    RawTraceRehydrator,
    RawTraceUnavailable,
)
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
    safe = redact_tool_data(raw_output, admin=False, kind="step_output")
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
    safe = redact_tool_data(act.output, admin=False, kind="step_output")
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
    safe = redact_tool_data(act.output, admin=False, kind="step_output")

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
        projector_version=instance.projector_version,
    )
    assert full["body"] == "TRIG"  # raw trigger restored from the vault


# --- G-Trace-Agreement: the trigger's version-aware agreement contract ------
#
# Reviewer, round-15 return: *"trigger recovery should have an explicit,
# version-aware projection-agreement contract. A redaction marker helps
# identify missing data but does not establish agreement."* Until
# 2026-09-19 `rehydrate_trigger` checked only for a `_redacted` marker,
# which detects ABSENCE and not DISAGREEMENT — a vault object that did not
# belong to the row was accepted and the run resumed on it. Step outputs
# and audit details have had this check since R13.


async def test_a_trigger_whose_vault_raw_does_not_match_the_row_FAILS_CLOSED() -> None:
    """THE GAP. The marker says raw exists; only re-projection says it is
    THIS row's raw. A substituted payload must not resume the run."""
    engine, instance, _ = await _run()
    rehydrator = RawTraceRehydrator(engine.repositories)

    with pytest.raises(RawTraceUnavailable, match="projection disagreement"):
        await rehydrator.rehydrate_trigger(
            purpose="resume",
            org_id=instance.org_id,
            instance_id=instance.id,
            # Not what `safe_trigger_payload` produces from the vaulted raw.
            safe_trigger={"_redacted": "routing only", "message_id": "SYNTHETIC-not-this-row"},
            projector_version=PROJECTOR_VERSION,
        )

    entries = await engine.repositories.audit.list_by_instance(instance.id)
    completed = [e for e in entries if e.action == "raw_trace_system_access_completed"]
    assert completed, "the failed recovery recorded no completion"
    assert completed[-1].detail["outcome"] == "integrity_failed"


async def test_an_older_projector_version_reads_unsupported_not_corrupt() -> None:
    """Criterion 17, the rule all three surfaces share: a projector bump must
    not make pre-change rows read as TAMPERED. The caller degrades with an
    explicit audited outcome instead."""
    engine, instance, _ = await _run()
    safe = safe_trigger_payload({"body": "TRIG"})

    full = await RawTraceRehydrator(engine.repositories).rehydrate_trigger(
        purpose="resume",
        org_id=instance.org_id,
        instance_id=instance.id,
        safe_trigger=safe,
        projector_version="1",  # a version this build cannot reproduce
    )
    assert full["body"] == "TRIG", "an unsupported version must still return the raw"

    entries = await engine.repositories.audit.list_by_instance(instance.id)
    completed = [e for e in entries if e.action == "raw_trace_system_access_completed"]
    assert completed[-1].detail["outcome"] == PROJECTION_UNSUPPORTED


async def test_an_agreeing_trigger_still_succeeds() -> None:
    """The counterpart. A check that refuses everything is not a check —
    the honest safe payload must still verify and return the raw."""
    engine, instance, _ = await _run()
    safe = safe_trigger_payload({"body": "TRIG"})

    full = await RawTraceRehydrator(engine.repositories).rehydrate_trigger(
        purpose="resume",
        org_id=instance.org_id,
        instance_id=instance.id,
        safe_trigger=safe,
        projector_version=PROJECTOR_VERSION,
    )
    assert full["body"] == "TRIG"
    entries = await engine.repositories.audit.list_by_instance(instance.id)
    completed = [e for e in entries if e.action == "raw_trace_system_access_completed"]
    assert completed[-1].detail["outcome"] == "succeeded"


def test_the_three_surfaces_share_one_version_gate() -> None:
    """R13 finding 5 was two surfaces asking the same question separately
    and coming to disagree. The third must not re-ask it either."""
    import inspect

    from workflow_platform import trace_rehydrate as tr

    for fn in (
        tr.verify_projection_agreement,
        tr.verify_audit_projection_agreement,
        tr.verify_trigger_projection_agreement,
    ):
        assert "_version_reproducible(" in inspect.getsource(fn), (
            f"{fn.__name__} does not go through the shared version gate"
        )


def test_the_instance_stamp_is_never_rewritten_after_creation() -> None:
    """WHY the instance stamp is the authoritative one for a trigger, pinned
    because the answer depends on a property of OTHER code.

    `_mark_instance` runs on every state transition. If it restamped, a
    resume on a newer build would relabel an instance whose trigger an older
    projector wrote, and this check would turn a version difference into a
    spurious `mismatch` — the exact failure criterion 17 exists to prevent.
    """
    import inspect

    from workflow_platform.engine.executor import WorkflowEngine

    body = inspect.getsource(WorkflowEngine._mark_instance)
    assert "projector_version" not in body and "_stamp_projection" not in body, (
        "_mark_instance now touches the projection stamp; the trigger agreement "
        "check keys off it and would read a re-stamped instance as tampered"
    )


async def test_a_flipped_run_stamps_the_instance_with_the_current_projector() -> None:
    """The claim the trigger check rests on, end to end rather than argued:
    under the flip, the instance carries the stamp of the projector that
    wrote its trigger, so `instance.projector_version` is a usable input and
    not always None."""
    engine = _engine()
    engine.trace_safe_only = True
    instance = await engine.run(load_definition(_DEF), trigger_payload={"body": "TRIG"})

    fresh = await engine.repositories.instances.get(instance.id)
    assert fresh is not None
    assert fresh.projector_version == PROJECTOR_VERSION

    full = await RawTraceRehydrator(engine.repositories).rehydrate_trigger(
        purpose="resume",
        org_id=fresh.org_id,
        instance_id=fresh.id,
        safe_trigger=fresh.trigger_payload,
        projector_version=fresh.projector_version,
    )
    assert full["body"] == "TRIG"
    entries = await engine.repositories.audit.list_by_instance(fresh.id)
    completed = [e for e in entries if e.action == "raw_trace_system_access_completed"]
    assert completed[-1].detail["outcome"] == "succeeded", (
        "a real flipped run's own stamp and trigger must agree"
    )
