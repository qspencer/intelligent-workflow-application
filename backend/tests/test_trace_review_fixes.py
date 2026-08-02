"""Regression tests for the external code review (2026-08-02). Each reproduces
a bypass the reviewer found and asserts it is now closed. New fixes append
here as they land."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.auth.raw_trace_grants import (
    ApproverConflict,
    DuplicateActiveGrant,
    MissingApprovalArtifact,
    RawTraceGrantService,
)
from workflow_platform.engine import FunctionRegistry, StepFailure, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import (
    RawTrace,
    RawTraceKind,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.persistence.models import RawTraceApprovalMode, RawTraceReasonCode
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


# --- F8: release audit reflects the ACTUAL retrieval outcome ---


async def test_f8_release_outcome_is_honest_after_fetch() -> None:
    from workflow_platform.api.raw_trace_audit import begin_raw_release, commit_raw_release

    repos = in_memory_repositories()
    rid, reason = await begin_raw_release(
        repos,
        raw_ok=True,
        surface="detail",
        actor_id="a",
        instance_id="i",
        kinds=["output", "error"],
    )
    assert rid is not None and reason is None
    # some kinds missing → partial (NOT "released")
    ok, r = await commit_raw_release(
        repos,
        request_id=rid,
        surface="detail",
        actor_id="a",
        instance_id="i",
        returned_kinds=["output"],
        withheld_kinds=["error"],
    )
    assert ok and r == "partial"
    # nothing retrieved → retrieval_failed
    ok2, r2 = await commit_raw_release(
        repos,
        request_id=rid,
        surface="detail",
        actor_id="a",
        instance_id="i",
        returned_kinds=[],
        withheld_kinds=["output", "error"],
    )
    assert ok2 and r2 == "retrieval_failed"
    # the release_decided audit records the REAL outcome, never a false "released"
    decided = [
        e
        for e in await repos.audit.list_by_instance("i")
        if e.action == "raw_trace_release_decided"
    ]
    outcomes = {e.detail.get("outcome") for e in decided}
    assert outcomes == {"partial", "retrieval_failed"} and "released" not in outcomes


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


# --- F4 + F5: dual-control bypass + atomic activation ---


def _grant_service() -> RawTraceGrantService:
    return RawTraceGrantService(in_memory_repositories(), require_dual_control=False)


async def test_f4_tenant_authorized_cannot_be_self_activated() -> None:
    from datetime import UTC, datetime, timedelta

    svc = _grant_service()
    now = datetime(2026, 8, 2, tzinfo=UTC)
    pending = await svc.request(
        principal_id="p",
        org_id=None,
        requested_by="admin-a",
        reason_code=RawTraceReasonCode.DEBUGGING,
        approval_mode=RawTraceApprovalMode.TENANT_AUTHORIZED,
        external_approval_ref="ticket-1",
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    # the requester (the only admin so far) cannot activate their own request
    with pytest.raises(ApproverConflict):
        await svc.approve(grant_id=pending.id, approved_by="admin-a", now=now)


async def test_f4_rejects_free_text_approval_ref() -> None:
    from datetime import UTC, datetime, timedelta

    svc = _grant_service()
    now = datetime(2026, 8, 2, tzinfo=UTC)
    with pytest.raises(MissingApprovalArtifact):
        await svc.request(
            principal_id="p",
            org_id=None,
            requested_by="admin-a",
            reason_code=RawTraceReasonCode.DEBUGGING,
            approval_mode=RawTraceApprovalMode.TENANT_AUTHORIZED,
            external_approval_ref="see the email I sent about the customer",  # free text
            expires_at=now + timedelta(hours=1),
            now=now,
        )


async def test_f5_concurrent_platform_activations_only_one_wins() -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta

    svc = _grant_service()
    now = datetime(2026, 8, 2, tzinfo=UTC)
    kw = {
        "principal_id": "p",
        "org_id": None,
        "requested_by": "admin-a",
        "reason_code": RawTraceReasonCode.DEBUGGING,
        "expires_at": now + timedelta(hours=1),
        "now": now,
    }
    g1 = await svc.request(**kw)  # type: ignore[arg-type]
    g2 = await svc.request(**kw)  # type: ignore[arg-type]
    results = await asyncio.gather(
        svc.approve(grant_id=g1.id, approved_by="admin-b", now=now),
        svc.approve(grant_id=g2.id, approved_by="admin-c", now=now),
        return_exceptions=True,
    )
    active = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(active) == 1  # exactly one activation wins
    assert len(errs) == 1 and isinstance(errs[0], DuplicateActiveGrant)


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
