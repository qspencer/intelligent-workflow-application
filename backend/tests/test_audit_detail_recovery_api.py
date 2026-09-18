"""Round-12 finding 1: a grant holder must actually GET the vaulted detail.

The write path shipped without the read path. The audit endpoints returned
the STORED entry, which after the at-rest tightening IS the projection — so a
grant holder received `{"_withheld_keys": true}` while the release log
recorded `released`. The vault row existed the whole time and nothing read it.

Tested THROUGH THE API and WITH ENCRYPTION ENABLED, per the return, including
the case where the vault record is unavailable.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.auth.raw_trace_grants import RawTraceGrantService
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.models import (
    RawTraceKind,
    RawTraceReasonCode,
    User,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.world import mock_world

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}

RAW_DETAIL = {"tool": "exfiltrate_sk_live_abc", "attempted": "/etc/shadow"}


async def _grant_platform_wide(repos: Any, principal_sub: str) -> None:
    """Platform-wide ACTIVE grant, authorized by two distinct Administrators."""
    row = await repos.users.get_by_identity("dev", principal_sub)
    assert row is not None
    svc = RawTraceGrantService(repos)
    grant = await svc.request(
        principal_id=row.id,
        org_id=None,
        requested_by="grantor-1",
        reason_code=RawTraceReasonCode.DEBUGGING,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    await svc.approve(grant_id=grant.id, approved_by="grantor-2")


@pytest.fixture
def encrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract B1: a real master key, so the vault payload is SEALED and the
    recovery path must decrypt with the right AEAD identity."""
    from workflow_platform import trace_cipher

    monkeypatch.setattr(trace_cipher, "_installed_key", None)
    monkeypatch.setenv(trace_cipher.ENV_MASTER_KEY, base64.b64encode(os.urandom(32)).decode())


async def _setup(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, str, str]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.RUNNING)
    )
    await engine._audit(
        "tool_param_override_blocked",
        actor_type="agent",
        actor_id="a",
        instance_id=instance.id,
        detail=RAW_DETAIL,
    )
    entries = await repos.audit.list_by_instance(instance.id)
    entry_id = next(e.id for e in entries if e.action == "tool_param_override_blocked")
    await repos.users.save(User(iss="dev", sub="root", org_id="default", roles=["Administrator"]))
    app = create_app(repositories=repos, engine=engine)
    return TestClient(app), repos, instance.id, entry_id


async def test_a_grant_holder_RECOVERS_the_vaulted_detail_through_the_api(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    client, repos, iid, _ = await _setup(monkeypatch)

    # Without a grant: projected, and the tool name must not appear.
    below = client.get(f"/api/workflow-instances/{iid}/audit", headers=_ADMIN).json()
    blocked = [e for e in below if e["action"] == "tool_param_override_blocked"]
    assert blocked, "the entry is missing entirely"
    assert "exfiltrate_sk_live_abc" not in str(blocked[0]["detail"]), (
        "a reader WITHOUT a grant recovered the raw"
    )

    # With a grant: the full detail comes back from the vault.
    await _grant_platform_wide(repos, "root")
    above = client.get(f"/api/workflow-instances/{iid}/audit", headers=_ADMIN).json()
    blocked = [e for e in above if e["action"] == "tool_param_override_blocked"]
    assert blocked[0]["detail"] == RAW_DETAIL, (
        "the grant holder did NOT recover the vaulted detail; got "
        f"{blocked[0]['detail']!r}. This is the round-12 finding: the endpoint "
        "returned the stored projection and logged it as released."
    )


async def test_the_release_log_records_what_was_ACTUALLY_retrieved(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """The half that made the bug invisible: the log said `released` whatever
    happened. With the vault row destroyed, the reader must still be denied
    the raw AND the outcome must not claim a release."""
    client, repos, iid, _entry_id = await _setup(monkeypatch)
    await _grant_platform_wide(repos, "root")

    # Destroy the vault row — the "unavailable record" case from the return.
    rows = await repos.raw_trace_vault.list_by_instance(iid)
    target = [r for r in rows if r.kind is RawTraceKind.AUDIT_DETAIL]
    assert target, "premise: a vault row existed to remove"
    store = repos.raw_trace_vault
    for r in target:
        # Simulate a DB operator deleting the vault object. The entry's
        # projector_version stamp SURVIVES, which is the whole point: the
        # reader still knows raw was vaulted and must report that it could
        # not produce it, rather than quietly serving the projection as if
        # nothing were missing.
        store._items.pop(r.id, None)
        store._by_key.pop(r.idempotency_key, None)

    body = client.get(f"/api/workflow-instances/{iid}/audit", headers=_ADMIN).json()
    blocked = [e for e in body if e["action"] == "tool_param_override_blocked"]
    assert "exfiltrate_sk_live_abc" not in str(blocked[0]["detail"]), (
        "raw appeared even though the vault row was gone"
    )
    decided = [
        e
        for e in await repos.audit.list_by_instance(iid)
        if e.action == "raw_trace_release_decided"
    ]
    assert decided, "no release decision was recorded at all"
    outcomes = {str(e.detail.get("outcome")) for e in decided}
    assert outcomes != {"released"}, (
        f"the release log claims {outcomes} but nothing was retrieved — this is the "
        "exact mis-reporting the round-12 return identified"
    )


async def test_an_entry_that_lost_NOTHING_is_returned_without_a_vault_fetch(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """Recovery is scoped by the same predicate the writer used, so an entry
    with nothing vaulted must not be reported as a failed retrieval."""
    client, repos, iid, _ = await _setup(monkeypatch)
    await _grant_platform_wide(repos, "root")
    body = client.get(f"/api/workflow-instances/{iid}/audit", headers=_ADMIN).json()
    assert body, "no entries came back"
    decided = [
        e
        for e in await repos.audit.list_by_instance(iid)
        if e.action == "raw_trace_release_decided"
    ]
    assert not any(str(e.detail.get("outcome")) == "retrieval_failed" for e in decided), (
        "an entry with nothing vaulted was counted as a retrieval failure"
    )


#: The functions that ARE the recovery path.
RECOVERY_HELPERS = {"_audit_response", "_release_audit"}

#: Handlers that read the audit log but expose no detail to a reader, with
#: the reason. An exclusion must be argued, never assumed.
AUDIT_READERS_EXEMPT = {
    # Matches `escalation_resolved.original_id` (a DECLARED field, so it
    # survives projection) and returns only a status. No detail reaches the
    # caller, so there is nothing to recover.
    "resolve_escalation",
}


def test_every_endpoint_returning_audit_entries_goes_through_recovery() -> None:
    """R13 self-audit, and the structural fix for the round-12 class.

    Round 12 returned because the write path shipped without the read path.
    Fixing it, I updated two of the THREE endpoints that return audit
    entries — the global `/audit` branch kept the defect. Enumerate instead
    of remembering: any route handler whose return type is
    `list[AuditEntry]` must call `_audit_response`, which is the only place
    that fetches from the vault and records the real outcome.
    """
    import ast
    import pathlib

    src = pathlib.Path("src/workflow_platform/api/workflows.py")
    tree = ast.parse(src.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # R13 finding 1: keying on the RETURN TYPE missed `/api/escalations`,
        # which returns `list[dict]` built from audit entries. Key on what
        # the handler DOES instead — reads the audit log — which is the
        # property that makes recovery necessary. Same lesson as the audit
        # writer inventory: match the behaviour, not the spelling.
        body = ast.unparse(node)
        reads_audit = "repositories.audit.list_" in body
        if not reads_audit:
            continue
        if node.name in RECOVERY_HELPERS or node.name in AUDIT_READERS_EXEMPT:
            continue
        called = {
            c.func.id
            for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }
        # Either use a recovery helper, or run the two-phase release
        # yourself (`explain_step` does, and correctly).
        if not ((RECOVERY_HELPERS | {"begin_raw_release"}) & called):
            offenders.append(node.name)
    assert not offenders, (
        f"these handlers return audit entries without going through _audit_response: "
        f"{offenders}. They will hand a grant holder the STORED projection and log it "
        "as a release — the round-12 finding, reintroduced."
    )


async def test_the_GLOBAL_audit_list_also_recovers(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """The branch I missed when fixing round 12. A platform-wide grant holder
    reading `/api/audit` (no instance filter) must get the raw too."""
    client, repos, _iid, _ = await _setup(monkeypatch)
    await _grant_platform_wide(repos, "root")
    body = client.get("/api/audit?limit=200", headers=_ADMIN).json()
    blocked = [e for e in body if e["action"] == "tool_param_override_blocked"]
    assert blocked, "the entry is missing from the global list"
    assert blocked[0]["detail"] == RAW_DETAIL, (
        f"the global list did not recover the vaulted detail; got {blocked[0]['detail']!r}"
    )


async def test_the_WS_stream_also_recovers_for_a_grant_holder(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """R13 self-audit, third surface — tested THROUGH the websocket (R-e).

    `_redact_ws_event(event) == event` used to mean "no raw here". Since the
    at-rest tightening the PUBLISHED event is already the projection, so that
    was true for every audit event and the stream quietly stopped offering
    raw to grant holders — and skipped the access audit with it. The
    persisted stamp is the honest signal.
    """
    from workflow_platform.events import EventBus

    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    events = EventBus()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
        events=events,
    )
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.RUNNING)
    )
    await repos.users.save(User(iss="dev", sub="root", org_id="default", roles=["Administrator"]))
    await _grant_platform_wide(repos, "root")
    app = create_app(repositories=repos, engine=engine, events=events)
    client = TestClient(app)

    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=instance.id,
            detail=RAW_DETAIL,
        )
        frame = ws.receive_json()

    assert frame["detail"] == RAW_DETAIL, (
        f"the grant holder did not receive the vaulted detail over WS; got {frame['detail']!r}"
    )


async def test_the_ESCALATION_endpoint_recovers_for_a_grant_holder(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """R13 finding 1, through the endpoint (R-e).

    `/api/escalations` returned `e.detail.get("reason")` — the STORED detail,
    which since the tightening has reason/context withheld. A grant holder
    received `reason: null` WITH `raw_included: true`, while the same entry's
    full detail came back fine from the audit endpoint.
    """
    from workflow_platform.tools.base import ToolContext
    from workflow_platform.tools.escalation import RequestHumanReviewTool

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("WORKFLOW_PLATFORM_TRACE_SAFE_ONLY", "1")
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.RUNNING)
    )
    tool = RequestHumanReviewTool(repos.audit, repositories=repos)
    result = await tool.execute(
        {"reason": "SYNTHETIC-REASON", "context": {"body": "SYNTHETIC-BODY"}},
        ToolContext(world=mock_world(), agent_id="act", workflow_instance_id=instance.id),
    )
    assert result.error is None, f"the escalation was refused: {result.error}"

    await repos.users.save(User(iss="dev", sub="root", org_id="default", roles=["Administrator"]))
    client = TestClient(create_app(repositories=repos, engine=engine))

    below = client.get("/api/escalations", headers=_ADMIN).json()
    assert below and below[0]["raw_included"] is False
    assert "SYNTHETIC-REASON" not in str(below)

    await _grant_platform_wide(repos, "root")
    above = client.get("/api/escalations", headers=_ADMIN).json()
    assert above[0]["raw_included"] is True, "raw_included is false for a grant holder"
    assert above[0]["reason"] == "SYNTHETIC-REASON", (
        f"the grant holder did not recover the escalation reason; got {above[0]['reason']!r}"
    )
    assert above[0]["context"] == {"body": "SYNTHETIC-BODY"}


async def test_the_escalation_tool_REFUSES_rather_than_discarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13 finding 3: constructed without repositories, the tool wrote a
    projected escalation and no vault row — destroying the model-authored
    reason it exists to carry. Optional preservation is not preservation."""
    from workflow_platform.tools.base import ToolContext
    from workflow_platform.tools.escalation import RequestHumanReviewTool

    monkeypatch.setenv("WORKFLOW_PLATFORM_TRACE_SAFE_ONLY", "1")
    repos = in_memory_repositories()
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.RUNNING)
    )
    ctx = ToolContext(world=mock_world(), agent_id="act", workflow_instance_id=instance.id)

    no_repos = RequestHumanReviewTool(repos.audit)
    result = await no_repos.execute({"reason": "SYNTHETIC", "context": {}}, ctx)
    assert result.error and "without the repositories" in result.error
    assert not await repos.audit.list_recent(limit=10), "it appended anyway"

    # And with no instance to vault against.
    with_repos = RequestHumanReviewTool(repos.audit, repositories=repos)
    result = await with_repos.execute(
        {"reason": "SYNTHETIC", "context": {}},
        ToolContext(world=mock_world(), agent_id="act"),
    )
    assert result.error and "no workflow instance" in result.error


async def test_the_WS_release_record_matches_what_was_DELIVERED(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """R13 finding 2, the reviewer's exact scenario.

    With the vault record removed the socket correctly delivered a withheld
    frame — but the release record still said `released`, because WS used the
    ATOMIC release which commits before anything is fetched. A separate
    system-access record said `retrieval_failed`, so the two disagreed about
    the same event.
    """
    from workflow_platform.events import EventBus
    from workflow_platform.persistence.models import RawTraceKind

    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    events = EventBus()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
        events=events,
    )
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="default", state=WorkflowInstanceState.RUNNING)
    )
    await repos.users.save(User(iss="dev", sub="root", org_id="default", roles=["Administrator"]))
    await _grant_platform_wide(repos, "root")
    client = TestClient(create_app(repositories=repos, engine=engine, events=events))

    # Vault a detail, then DESTROY the vault row: recovery must fail.
    await engine._audit(
        "tool_param_override_blocked",
        actor_type="agent",
        actor_id="a",
        instance_id=instance.id,
        detail=RAW_DETAIL,
        entry_id="ws-entry",
    )

    async def _destroy_audit_rows() -> None:
        """Simulate a DB operator deleting the vault objects. Typed loosely
        because it reaches into the in-memory repo's internals on purpose."""
        store: Any = repos.raw_trace_vault
        for r in await store.list_by_instance(instance.id):
            if r.kind is RawTraceKind.AUDIT_DETAIL:
                store._items.pop(r.id, None)
                store._by_key.pop(r.idempotency_key, None)

    await _destroy_audit_rows()

    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=instance.id,
            detail=RAW_DETAIL,
            entry_id="ws-entry-2",
        )
        # Destroy the second one's row too, between publish and delivery.
        await _destroy_audit_rows()
        frame = ws.receive_json()

    assert "exfiltrate_sk_live_abc" not in str(frame), "raw was delivered with no vault row"
    ws_decisions = [
        e
        for e in await repos.audit.list_recent(limit=200)
        if e.action == "raw_trace_release_decided" and e.detail.get("surface") == "ws"
    ]
    assert ws_decisions, "the websocket recorded no release decision at all"
    assert not all(str(e.detail.get("outcome")) == "released" for e in ws_decisions), (
        f"the WS release record claims released but nothing was retrieved: "
        f"{[e.detail.get('outcome') for e in ws_decisions]}"
    )


async def test_an_undecryptable_record_completes_the_release_audit(
    monkeypatch: pytest.MonkeyPatch, encrypted: None
) -> None:
    """R13 finding 4, the half my first fix missed.

    Normalising `TraceCipherError` stopped the HTTP 500. But `_payload_of`
    raising between `_begin` and `_complete` still left the system-access
    record OPEN — the reader gets nothing and the log never says why, which
    is the worst of both. The endpoint must return the projected entry AND
    the release audit must complete.
    """
    client, repos, iid, _ = await _setup(monkeypatch)
    await _grant_platform_wide(repos, "root")

    # Corrupt the sealed payload so decryption fails (not merely absent).
    from workflow_platform.persistence.models import RawTraceKind

    store = repos.raw_trace_vault
    rows = [r for r in await store.list_by_instance(iid) if r.kind is RawTraceKind.AUDIT_DETAIL]
    assert rows, "premise: a sealed vault row exists"
    for r in rows:
        broken = dict(r.payload)
        broken["ct"] = base64.b64encode(b"not-the-real-ciphertext").decode()
        store._items[r.id] = r.model_copy(update={"payload": broken})

    resp = client.get(f"/api/workflow-instances/{iid}/audit", headers=_ADMIN)
    assert resp.status_code == 200, f"an undecryptable record produced HTTP {resp.status_code}"
    assert "exfiltrate_sk_live_abc" not in resp.text

    entries = await repos.audit.list_by_instance(iid)
    attempted = [e for e in entries if e.action == "raw_trace_system_access_attempted"]
    completed = [e for e in entries if e.action == "raw_trace_system_access_completed"]
    assert attempted, "no access attempt was recorded"
    assert len(completed) >= len(attempted), (
        f"{len(attempted)} access attempts but only {len(completed)} completions — "
        "a decryption failure left the access record open"
    )
    assert any(str(e.detail.get("outcome")) == "retrieval_failed" for e in completed), (
        f"no completion says retrieval_failed: {[e.detail.get('outcome') for e in completed]}"
    )
