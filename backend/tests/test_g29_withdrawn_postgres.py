"""G29 withdrawal — the REPOSITORY LOADING path, against a real Postgres.

The external reviewer's closure (2026-10-29) asked that every persistence path
be shown to revalidate, "including clone, import, scaffold, update, and
repository loading". `test_g29_withdrawn.py` covers every path that WRITES a
definition (derived from source; `save` is an upsert, so update is one of
them). This covers the path that READS one: a row written some other way — raw
SQL, an older build, a restore — must not load as a live definition carrying a
released vocabulary.

Fail-closed by design: a row that cannot validate is never served, and because
`list_all` validates every row, one such row makes listing fail rather than
quietly omitting or serving it. Pinned here so changing that is deliberate.

Integration-marked: `TEST_DATABASE_URL` names a database whose tables this
DROPS. Never point it at a database you need.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.persistence.sqlalchemy_models import Base
from workflow_platform.workflow import load_definition

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
async def test_a_stored_declaration_is_refused_on_load(engine: AsyncEngine) -> None:
    repos = postgres_repositories(make_session_factory(engine))
    await repos.definitions.save(
        load_definition(
            {
                "id": "g29-load-probe",
                "name": "g29 load probe",
                "trigger": {"type": "manual"},
                "steps": [],
                "edges": [],
            }
        )
    )
    assert await repos.definitions.get("g29-load-probe") is not None

    # Written around the API, the way an older build or a restore would.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE workflow_definitions SET body = jsonb_set(body, '{released_fields}', "
                '\'{"category": ["operator-looking-token"]}\'::jsonb) WHERE id = :id'
            ),
            {"id": "g29-load-probe"},
        )

    with pytest.raises(ValueError, match="released_fields is withdrawn"):
        await repos.definitions.get("g29-load-probe")
    with pytest.raises(ValueError, match="released_fields is withdrawn"):
        await repos.definitions.list_all()
