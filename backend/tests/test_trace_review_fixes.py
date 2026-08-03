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
    InvalidGrantTransition,
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
from workflow_platform.persistence.models import (
    RawTraceApprovalMode,
    RawTraceGrantState,
    RawTraceReasonCode,
)
from workflow_platform.trace_cipher import ENV_MASTER_KEY
from workflow_platform.trace_projection import (
    PROJECTOR_VERSION,
    redact_error,
    redact_tool_data,
)
from workflow_platform.trace_rehydrate import RawTraceRehydrator, RawTraceUnavailable
from workflow_platform.trace_vault import RawTraceVault, idempotency_key
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET = "REVIEW-FIX-SECRET"
RAW_ERR = "BOOM-RAW-ERROR-SENTINEL"
_KEY = base64.b64encode(b"review-fixes-master-key-32-byte!").decode()


# --- Re-review (2026-08-03) — contained fixes ---


def test_rr_f1_forged_redacted_marker_is_not_trusted() -> None:
    from workflow_platform.trace_projection import safe_tool_call

    forged = {
        "name": "mail",
        "input": {"body": SECRET},
        "result": {"content": SECRET},
        "_redacted": "forged",
    }
    safe = safe_tool_call(forged)
    assert SECRET not in str(safe)
    assert "input" not in safe and "result" not in safe


def test_rr_p1_registered_key_with_unvalidated_value_is_redacted() -> None:
    """The re-review's three reproduced leaks: a KEY allowlist passed values
    without validating them. Now every survivor passes its field validator."""
    # a registered field carrying prose instead of its declared shape
    assert redact_tool_data({"usage": [SECRET]}, admin=False)["usage"] != [SECRET]
    assert redact_tool_data({"model": f"{SECRET} with spaces"}, admin=False)["model"].startswith(
        "[redacted"
    )
    # an UNREGISTERED numeric field no longer passes "safe by type"
    assert redact_tool_data({"ssn": 123456789}, admin=False)["ssn"] != 123456789
    # a per-workflow business vocabulary is not platform-registered
    assert redact_tool_data({"category": SECRET}, admin=False)["category"].startswith("[redacted")
    # …while a correctly-shaped registered value still survives
    assert redact_tool_data({"usage": {"input_tokens": 12}}, admin=False)["usage"] == {
        "input_tokens": 12
    }


async def test_rr_f4_sealed_row_without_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import workflow_platform.trace_cipher as tc

    monkeypatch.setenv(ENV_MASTER_KEY, _KEY)
    tc._installed_key = None
    repos = in_memory_repositories()
    await RawTraceVault(repos).record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output={"summary": SECRET}
    )
    row = (await repos.raw_trace_vault.list_by_instance("i"))[0]
    # key vanishes; a rehydrator with no cipher must NOT return the envelope
    monkeypatch.delenv(ENV_MASTER_KEY, raising=False)
    tc._installed_key = None
    with pytest.raises(RawTraceUnavailable):
        RawTraceRehydrator(repos)._payload_of(
            row, org_id="o", instance_id="i", step_attempt_id="s", kind="output"
        )


async def test_rr_f6_tenant_authorized_never_immediately_activates() -> None:
    svc = _grant_service()  # dual-control OFF
    g = await svc.request(
        principal_id="p",
        org_id="org1",  # org-scoped → would take the immediate path for dual_admin
        requested_by="admin-a",
        reason_code=RawTraceReasonCode.DEBUGGING,
        approval_mode=RawTraceApprovalMode.TENANT_AUTHORIZED,
        external_approval_ref="ticket-1",
    )
    assert g.state.value == "pending"  # needs the artifact + a distinct activator


async def test_rr_f6_ticket_ref_must_be_opaque() -> None:
    svc = _grant_service()
    with pytest.raises(MissingApprovalArtifact):
        await svc.request(
            principal_id="p",
            org_id="org1",
            requested_by="admin-a",
            reason_code=RawTraceReasonCode.DEBUGGING,
            ticket_ref="see the email about the customer account",  # free text
        )


# --- F1: default-deny projector (no-tool output + arbitrary fields) ---


def test_f1_no_tool_output_text_is_redacted() -> None:
    # the reviewer's repro: a step that used NO tool still leaks output_text
    safe = redact_tool_data({"output_text": SECRET, "model": "claude-haiku-4-5"}, admin=False)
    assert SECRET not in str(safe["output_text"])
    assert safe["model"] == "claude-haiku-4-5"  # registered + validated survives


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
    assert safe["cost_usd"] == 0.5  # registered + bounded numeric survives


async def test_f1_redacted_field_is_vaulted_and_rehydrated_lossless() -> None:
    # a free-form business field is redacted below-grant AND recoverable
    repos = in_memory_repositories()
    output = {"model": "claude-haiku-4-5", "summary": SECRET, "cost_usd": 0.01}
    await RawTraceVault(repos).record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output=output, durable=True
    )
    safe = redact_tool_data(output, admin=False)
    assert safe["model"] == "claude-haiku-4-5" and SECRET not in str(safe)
    full = await RawTraceRehydrator(repos).rehydrate_output(
        purpose="resume",
        org_id="o",
        instance_id="i",
        step_attempt_id="s",
        safe_output=safe,
        projector_version=PROJECTOR_VERSION,
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


# --- F10: exhaustive zero-raw verifier ---


async def test_f10_verifier_catches_error_raw_and_flags_audit() -> None:
    from workflow_platform.trace_migration import backfill_all, verify_zero_raw

    engine = _failing_engine(safe_only=False)  # raw error kept inline
    await engine.run(_failing_def(), trigger_payload={})
    repos = engine.repositories

    report = await verify_zero_raw(repos)
    cols = {(f.table, f.column) for f in report.findings}
    assert ("workflow_instances", "error") in cols  # instance error scanned (F2/F10)
    assert ("step_executions", "error") in cols  # step error scanned
    assert report.audit_findings  # append-only audit raw is surfaced, not ignored
    assert not report.clean

    # Backfill migrates the instance + step columns; append-only audit raw stays
    # (reported, so the gate still fails until it's encrypted/migrated).
    await backfill_all(repos)
    report2 = await verify_zero_raw(repos)
    assert [f for f in report2.findings if f.table != "audit_log"] == []


async def test_f10_capped_scan_never_certifies() -> None:
    from workflow_platform.trace_migration import verify_zero_raw

    engine = _failing_engine(safe_only=True)
    await engine.run(_failing_def(), trigger_payload={})
    await engine.run(_failing_def(), trigger_payload={})
    # A scan that hits its ceiling can't certify what it didn't read.
    report = await verify_zero_raw(engine.repositories, limit=1)
    assert report.capped and not report.clean


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


# --- P4: grant + vault compare-and-set (re-review finding 6 + 7) ---


async def test_p4_concurrent_revoke_is_not_overwritten_by_approve() -> None:
    """The resurrection race: approve() read a PENDING grant, a concurrent
    revoke() cancelled it, and approve()'s blind save overwrote the
    cancellation back to ACTIVE."""
    from datetime import UTC, datetime, timedelta

    svc = _grant_service()
    now = datetime(2026, 8, 3, tzinfo=UTC)
    g = await svc.request(
        principal_id="p",
        org_id=None,
        requested_by="admin-a",
        reason_code=RawTraceReasonCode.DEBUGGING,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    # the concurrent cancel lands first
    await svc.revoke(grant_id=g.id, revoked_by="admin-c", now=now)
    with pytest.raises(InvalidGrantTransition):
        await svc.approve(grant_id=g.id, approved_by="admin-b", now=now)
    final = await svc._repos.raw_trace_grants.get(g.id)
    assert final is not None and final.state.value == "cancelled"  # NOT resurrected
    assert await svc.covering(principal_id="p", target_org="org1", now=now) is None


async def test_p4_in_memory_repo_does_real_cas() -> None:
    """The in-memory repo must actually compare-and-set — an impl that merely
    overwrites would give the suite false assurance about the Postgres path."""
    from datetime import UTC, datetime, timedelta

    repos = in_memory_repositories()
    svc = RawTraceGrantService(repos, require_dual_control=False)
    now = datetime(2026, 8, 3, tzinfo=UTC)
    g = await svc.request(
        principal_id="p",
        org_id=None,
        requested_by="admin-a",
        reason_code=RawTraceReasonCode.DEBUGGING,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    stored = await repos.raw_trace_grants.get(g.id)
    assert stored is not None
    stored.state = RawTraceGrantState.ACTIVE  # a real transition, not a no-op write
    assert await repos.raw_trace_grants.update_if(stored, expected_state="pending") is True
    # the stored row is ACTIVE now, so the same pending-CAS must FAIL
    assert await repos.raw_trace_grants.update_if(stored, expected_state="pending") is False
    # and the row was not clobbered back
    again = await repos.raw_trace_grants.get(g.id)
    assert again is not None and again.state is RawTraceGrantState.ACTIVE


async def test_p4_durable_vault_write_conflict_raises() -> None:
    """A different payload under the same idempotency key must raise, not
    silently retain the OLD raw while the engine believes it stored the new."""
    from workflow_platform.persistence.repository import VaultConflict

    repos = in_memory_repositories()
    vault = RawTraceVault(repos)
    await vault.record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output={"summary": "OLD-RAW"}
    )
    with pytest.raises(VaultConflict):
        await vault.record_step_output(
            org_id="o", instance_id="i", step_attempt_id="s", output={"summary": "NEW-RAW"}
        )


async def test_p4_same_content_re_vault_is_still_idempotent() -> None:
    repos = in_memory_repositories()
    vault = RawTraceVault(repos)
    out = {"summary": "SAME-RAW"}
    await vault.record_step_output(org_id="o", instance_id="i", step_attempt_id="s", output=out)
    await vault.record_step_output(org_id="o", instance_id="i", step_attempt_id="s", output=out)
    assert len(await repos.raw_trace_vault.list_by_instance("i")) == 1


# --- P3a: rehydration validator (§4.3 predicate, not marker scans) ---


async def test_p3a_deleting_a_marker_cannot_bypass_the_vault() -> None:
    """The reviewer's probe: replacing a projected operational row with
    marker-free content made rehydration skip the vault entirely and run on
    the tampered value, with no system-access audit. The PERSISTED stamp — not
    the payload's own content — now decides."""
    repos = in_memory_repositories()
    await RawTraceVault(repos).record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output={"summary": SECRET}, durable=True
    )
    tampered = {"summary": "operator-tampered-projection"}  # NO redaction marker
    with pytest.raises(RawTraceUnavailable):  # disagreement → integrity failure
        await RawTraceRehydrator(repos).rehydrate_output(
            purpose="resume",
            org_id="o",
            instance_id="i",
            step_attempt_id="s",
            safe_output=tampered,
            projector_version=PROJECTOR_VERSION,
        )


async def test_p3a_aborted_vault_row_is_rejected() -> None:
    from workflow_platform.persistence.models import RawTraceState

    repos = in_memory_repositories()
    await RawTraceVault(repos).record_step_output(
        org_id="o", instance_id="i", step_attempt_id="s", output={"summary": SECRET}, durable=True
    )
    row = (await repos.raw_trace_vault.list_by_instance("i"))[0]
    row.state = RawTraceState.ABORTED
    with pytest.raises(RawTraceUnavailable):
        RawTraceRehydrator(repos)._payload_of(
            row, org_id="o", instance_id="i", step_attempt_id="s", kind="output"
        )


def test_p3a_agreement_predicate() -> None:
    from workflow_platform.trace_rehydrate import verify_projection_agreement

    raw = {"summary": SECRET, "model": "claude-haiku-4-5"}
    stored = redact_tool_data(raw, admin=False)
    assert verify_projection_agreement(raw, stored, PROJECTOR_VERSION) == "ok"
    assert verify_projection_agreement(raw, {"summary": "other"}, PROJECTOR_VERSION) == "mismatch"
    # criterion 17: an older projector must NOT read as corrupt
    assert verify_projection_agreement(raw, stored, "1") == "unsupported"


async def test_p3a_unstamped_row_needs_no_vault() -> None:
    # a row never written as a projection (dark dual-write) holds raw inline
    out = await RawTraceRehydrator(in_memory_repositories()).rehydrate_output(
        purpose="resume",
        org_id="o",
        instance_id="i",
        step_attempt_id="s",
        safe_output={"summary": SECRET},
        projector_version=None,
    )
    assert out["summary"] == SECRET
