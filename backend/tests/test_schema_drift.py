"""Schema-drift detection (NEXT_STEPS G26.1).

The failure this guards against: `--reload` hot-loads code but not migrations,
so the code can reference a column the database does not have. It happened
twice; the second time every workflow run on the deployed box failed for four
days and nothing surfaced it. Tests build a fresh schema from the models, so
they are structurally incapable of catching it — hence an explicit comparison.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.schema_version import (
    assert_schema_current,
    check_schema_drift,
    expected_revisions,
)


def test_expected_revisions_reads_the_migration_scripts() -> None:
    heads = expected_revisions()
    assert heads, "should read at least one alembic head from disk"
    assert all(isinstance(h, str) and h for h in heads)


async def test_no_database_is_ok_not_unknown() -> None:
    """In-memory deployments have no schema to drift — that is `ok` with an
    explanation, not a scary `unknown`."""
    status = await check_schema_drift(None)
    assert status.ok and status.state == "ok"
    assert status.detail and "in-memory" in status.detail


class _FakeSession:
    def __init__(self, rev: str | None, boom: bool = False) -> None:
        self._rev, self._boom = rev, boom

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> Any:
        if self._boom:
            raise RuntimeError('relation "alembic_version" does not exist')
        rev = self._rev

        class _R:
            def first(self) -> tuple[str] | None:
                return (rev,) if rev is not None else None

        return _R()


def _factory(rev: str | None, boom: bool = False) -> Any:
    return lambda: _FakeSession(rev, boom)


async def test_matching_revision_is_ok() -> None:
    head = expected_revisions()[0]
    status = await check_schema_drift(_factory(head))
    assert status.ok and status.current == head


async def test_behind_revision_is_drift_and_names_both() -> None:
    """The exact production failure: DB at an older revision than the code."""
    status = await check_schema_drift(_factory("0008"))
    assert status.state == "drift" and not status.ok
    assert status.current == "0008"
    assert status.detail and "0008" in status.detail
    assert "upgrade head" in status.detail  # tells the operator what to DO


async def test_never_migrated_is_drift() -> None:
    status = await check_schema_drift(_factory(None))
    assert status.state == "drift"
    assert status.detail and "never been migrated" in status.detail


async def test_unreadable_version_table_is_unknown_not_ok() -> None:
    """Fail-visible: if we cannot tell, we must not report ok."""
    status = await check_schema_drift(_factory(None, boom=True))
    assert status.state == "unknown" and not status.ok


async def test_assert_exits_on_drift_and_warns_on_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await assert_schema_current(_factory("0008"))
    assert "schema drift" in str(exc.value)

    await assert_schema_current(_factory(None, boom=True))  # warns, does not exit
    assert "inconclusive" in capsys.readouterr().out


def test_health_reports_schema_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(repositories=in_memory_repositories(), start_triggers=False))
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["schema"]["state"] == "ok"
    assert body["schema"]["expected"], "health must report what revision the code expects"
