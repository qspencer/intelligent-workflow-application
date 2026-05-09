"""Smoke test for the FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from workflow_platform.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
