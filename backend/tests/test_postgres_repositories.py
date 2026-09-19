"""Postgres-backed repository integration test.

Skipped unless `TEST_DATABASE_URL` is set. CI runs this against a `services:`
Postgres; locally, set the env var to your dev database URL and run
`uv run pytest -m integration`.

Schema is recreated fresh per session via `Base.metadata.create_all`. The
Alembic migration's correctness is verified separately by an `alembic upgrade
head && alembic downgrade base` job in CI.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import WorkflowInstanceState
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.persistence.sqlalchemy_models import Base
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

skip_if_no_db = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL not set; skipping Postgres integration tests",
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert TEST_DATABASE_URL is not None
    engine = make_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@skip_if_no_db
async def test_workflow_persists_through_postgres(engine: AsyncEngine) -> None:
    session_factory = make_session_factory(engine)
    repos = postgres_repositories(session_factory)

    fns = FunctionRegistry()

    async def step_a(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"v": 1}

    async def step_b(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"v": ctx.steps["a"]["v"] + 1}

    fns.register("step_a", step_a)
    fns.register("step_b", step_b)

    definition = load_definition(
        {
            "id": "wf-postgres-test",
            "name": "Postgres test",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "step_a"},
                {"id": "b", "type": "deterministic", "function": "step_b"},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
    )
    await repos.definitions.save(definition)

    engine_runner = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    instance = await engine_runner.run(definition, trigger_payload={"src": "test"})
    assert instance.state == WorkflowInstanceState.COMPLETED

    fetched = await repos.instances.get(instance.id)
    assert fetched is not None
    assert fetched.context["steps"]["b"]["v"] == 2

    steps = await repos.steps.list_by_instance(instance.id)
    assert {s.step_id for s in steps} == {"a", "b"}

    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert actions[0] == "workflow_started"
    assert actions[-1] == "workflow_completed"


@skip_if_no_db
async def test_definition_round_trip_via_postgres(engine: AsyncEngine) -> None:
    session_factory: async_sessionmaker = make_session_factory(engine)  # type: ignore[type-arg]
    repos = postgres_repositories(session_factory)

    definition = load_definition(
        {
            "id": "round-trip",
            "name": "Round trip",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
            "edges": [],
        }
    )
    await repos.definitions.save(definition)
    fetched = await repos.definitions.get("round-trip")
    assert fetched is not None
    assert fetched.name == "Round trip"

    listed = await repos.definitions.list_all()
    assert {d.id for d in listed} == {"round-trip"}


@skip_if_no_db
async def test_trigger_cursor_upsert_round_trip(engine: AsyncEngine) -> None:
    """G9: cursor state persists and upserts (second set overwrites)."""
    from datetime import UTC, datetime

    from workflow_platform.persistence import TriggerCursorState

    repos = postgres_repositories(make_session_factory(engine))
    key = "email:wf-pg:me@example.com"
    assert await repos.trigger_cursors.get(key) is None

    first = TriggerCursorState(cursor=datetime(2026, 7, 13, 12, 0, tzinfo=UTC), seen_ids=["m-1"])
    await repos.trigger_cursors.set(key, first)
    second = TriggerCursorState(
        cursor=datetime(2026, 7, 14, 1, 30, tzinfo=UTC), seen_ids=["m-1", "m-2"]
    )
    await repos.trigger_cursors.set(key, second)

    loaded = await repos.trigger_cursors.get(key)
    assert loaded is not None
    assert loaded.cursor == second.cursor
    assert loaded.seen_ids == ["m-1", "m-2"]


@skip_if_no_db
async def test_audit_detail_vaulting_round_trips_through_a_REAL_database(
    engine: AsyncEngine,
) -> None:
    """The outage of 2026-09-18, end to end against Postgres.

    `audit_entry_id` was on the model and the table but absent from the INSERT
    and the row mapper, so it stored NULL, read back None, and
    `vault_fingerprint` compared unequal on the FIRST write — every audit vault
    put raised `VaultConflict`, which propagates out of `_audit` and fails the
    run. In-memory repositories round-trip the pydantic object, so only a real
    database exercises this.
    """
    from workflow_platform.persistence.models import (
        RawTraceKind,
        WorkflowInstance,
    )

    session_factory = make_session_factory(engine)
    repos = postgres_repositories(session_factory)
    wf_engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", org_id="acme", state=WorkflowInstanceState.RUNNING)
    )

    raw = {"tool": "exfiltrate_sk_live_abc", "attempted": "/etc/shadow"}
    # Two entries on ONE step attempt: the multiplicity case, which is also
    # what a step-attempt-keyed vault row would have collapsed.
    for _ in range(2):
        await wf_engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=instance.id,
            step_id="same-step",
            detail=raw,
        )

    rows = [
        r
        for r in await repos.raw_trace_vault.list_by_instance(instance.id)
        if r.kind is RawTraceKind.AUDIT_DETAIL
    ]
    assert len(rows) == 2, f"expected one vault row per audit entry, got {len(rows)}"
    assert all(r.audit_entry_id for r in rows), (
        "audit_entry_id did not survive the round trip through Postgres — "
        "the column is not mapped, and every put will raise VaultConflict"
    )
    assert len({r.audit_entry_id for r in rows}) == 2, "two entries collided onto one row"
    assert all(r.payload == raw for r in rows), "the raw detail is not recoverable"

    # And the audit entries themselves carry the ids the vault rows name.
    entries = [
        e
        for e in await repos.audit.list_recent(limit=50)
        if e.action == "tool_param_override_blocked"
    ]
    assert {e.id for e in entries} == {r.audit_entry_id for r in rows}


@skip_if_no_db
async def test_audit_append_is_idempotent_against_a_REAL_database(
    engine: AsyncEngine,
) -> None:
    """R13 finding 6, Postgres path.

    The in-memory repo cannot exhibit this (ledger M8): the Postgres append
    was an unconditional INSERT, so a retry after the append had committed
    either duplicated the row or raised a driver integrity error — neither
    of which the in-memory double reproduces.
    """
    from workflow_platform.persistence.models import AuditEntry
    from workflow_platform.persistence.repository import AuditConflict

    repos = postgres_repositories(make_session_factory(engine))
    entry = AuditEntry(
        id="idem-1",
        actor_type="agent",
        actor_id="a",
        action="tool_param_override_blocked",
        detail={"tool": "t", "attempted": "/etc/shadow"},
    )
    first = await repos.audit.append(entry)
    second = await repos.audit.append(entry)  # the retry
    assert first.id == second.id == "idem-1"

    rows = [e for e in await repos.audit.list_recent(limit=50) if e.id == "idem-1"]
    assert len(rows) == 1, f"the retry duplicated the row in Postgres: {len(rows)}"

    with pytest.raises(AuditConflict, match="different content"):
        await repos.audit.append(entry.model_copy(update={"detail": {"tool": "other"}}))


@skip_if_no_db
async def test_CONCURRENT_audit_appends_of_one_entry_do_not_race(
    engine: AsyncEngine,
) -> None:
    """R14 finding 4: check-then-insert across two transactions is a race.

    Two overlapping retries of the same logical write could both observe no
    row, and one then failed on the unique constraint despite carrying
    identical content. The insert itself must be the atomic step.
    """
    import asyncio as _asyncio

    from workflow_platform.persistence.models import AuditEntry
    from workflow_platform.persistence.repository import AuditConflict

    repos = postgres_repositories(make_session_factory(engine))
    entry = AuditEntry(
        id="race-1",
        actor_type="agent",
        actor_id="a",
        action="tool_param_override_blocked",
        detail={"tool": "t"},
    )
    results = await _asyncio.gather(
        *(repos.audit.append(entry) for _ in range(8)), return_exceptions=True
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, f"overlapping identical retries raised: {failures}"
    assert {r.id for r in results} == {"race-1"}  # type: ignore[union-attr]

    rows = [e for e in await repos.audit.list_recent(limit=50) if e.id == "race-1"]
    assert len(rows) == 1, f"concurrent appends produced {len(rows)} rows"

    # A conflicting reuse is still refused, concurrently or not.
    with pytest.raises(AuditConflict):
        await repos.audit.append(entry.model_copy(update={"detail": {"tool": "other"}}))


@skip_if_no_db
async def test_reseal_and_lookup_against_a_REAL_database(engine: AsyncEngine) -> None:
    """The same two methods against Postgres (M8).

    `reseal` writes through the ORM in its own transaction and
    `get_by_idempotency_key` reads by a unique index — neither shape is
    exercised by the in-memory double, which mutates a dict. Found by the
    round-15 step-0 counterpart check.
    """
    from workflow_platform.persistence.models import (
        RawTrace,
        RawTraceKind,
        WorkflowInstance,
    )

    repos = postgres_repositories(make_session_factory(engine))
    inst = await repos.instances.create(
        WorkflowInstance(id="i-reseal", workflow_id="wf", org_id="acme")
    )
    row = RawTrace(
        org_id="acme",
        instance_id=inst.id,
        audit_entry_id="e-1",
        kind=RawTraceKind.AUDIT_DETAIL,
        idempotency_key="k-reseal",
        payload={"sealed": "old"},
        content_commitment="commit-1",
    )
    await repos.raw_trace_vault.put(row)

    assert await repos.raw_trace_vault.reseal(
        row.id, payload={"sealed": "new"}, content_commitment="commit-1"
    )
    stored = await repos.raw_trace_vault.get_by_idempotency_key("k-reseal")
    assert stored is not None
    assert stored.payload == {"sealed": "new"}, "reseal did not persist through Postgres"
    assert stored.audit_entry_id == "e-1", "the entry binding was lost by reseal"
    assert not await repos.raw_trace_vault.reseal(
        "no-such-row", payload={}, content_commitment="c"
    )
