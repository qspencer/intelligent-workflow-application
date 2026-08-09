"""Smoke test for the FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from workflow_platform.main import app


def test_health_endpoint() -> None:
    """Smoke: the app answers and reports its schema state.

    This deliberately does NOT assert `status == "ok"`. Since G26.1 the status
    depends on whether the DATABASE_URL this app was built with is migrated —
    and `app` is constructed at import from the ambient environment. In the CI
    unit job that URL points at the Postgres service, which is intentionally
    unmigrated at that point (the Alembic step runs later), so `degraded` is the
    CORRECT answer there and `ok` locally. Pinning one of them made a true
    signal look like a failure for six consecutive runs.

    The semantics are covered deterministically in `test_schema_drift.py`, which
    controls its own environment. Here we assert only what is invariant."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code in (200, 503)
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert "version" in body
    assert body["schema"]["state"] in ("ok", "drift", "unknown")
    # 503 iff drift — the signal must not be silently decoupled from the status
    assert (response.status_code == 503) == (body["schema"]["state"] == "drift")
