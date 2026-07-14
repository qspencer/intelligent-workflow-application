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
