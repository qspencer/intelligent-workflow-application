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
