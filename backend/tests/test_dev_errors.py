"""Tests for the dev-only error-capture buffer + /api/dev/errors endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.observability import ErrorBuffer, ErrorCaptureHandler
from workflow_platform.persistence import in_memory_repositories


def _at(second: int) -> datetime:
    return datetime(2026, 6, 1, 12, 0, second, tzinfo=UTC)


# ---------- ErrorBuffer ----------


def test_buffer_dedups_repeats_into_a_count() -> None:
    buf = ErrorBuffer()
    for s in range(3):
        buf.record(level="ERROR", logger="x", message="boom", traceback=None, when=_at(s))
    snap = buf.snapshot()
    assert len(snap) == 1
    assert snap[0]["count"] == 3
    assert snap[0]["first_seen"] == _at(0).isoformat()
    assert snap[0]["last_seen"] == _at(2).isoformat()
    assert buf.total() == 3
    assert buf.distinct() == 1


def test_buffer_distinct_messages_are_separate_newest_first() -> None:
    buf = ErrorBuffer()
    buf.record(level="ERROR", logger="a", message="first", traceback=None, when=_at(0))
    buf.record(level="ERROR", logger="b", message="second", traceback=None, when=_at(1))
    snap = buf.snapshot()
    assert [e["message"] for e in snap] == ["second", "first"]  # most-recent-first
    assert buf.total() == 2


def test_buffer_respects_capacity() -> None:
    buf = ErrorBuffer(capacity=2)
    for i in range(3):
        buf.record(level="ERROR", logger="x", message=f"m{i}", traceback=None, when=_at(i))
    snap = buf.snapshot()
    assert [e["message"] for e in snap] == ["m2", "m1"]  # m0 evicted (oldest)
    assert buf.distinct() == 2


def test_buffer_clear() -> None:
    buf = ErrorBuffer()
    buf.record(level="ERROR", logger="x", message="boom", traceback=None, when=_at(0))
    buf.clear()
    assert buf.snapshot() == []
    assert buf.total() == 0


# ---------- ErrorCaptureHandler ----------


def test_handler_captures_errors_with_traceback_not_warnings() -> None:
    buf = ErrorBuffer()
    log = logging.getLogger("test.capture.handler")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    handler = ErrorCaptureHandler(buf)
    log.addHandler(handler)
    try:
        log.warning("a warning")  # below ERROR — ignored
        log.error("plain error")
        try:
            raise ValueError("kaboom")
        except ValueError:
            log.exception("with traceback")
    finally:
        log.removeHandler(handler)

    snap = buf.snapshot()
    messages = {e["message"] for e in snap}
    assert messages == {"plain error", "with traceback"}
    assert "a warning" not in messages
    exc = next(e for e in snap if e["message"] == "with traceback")
    assert exc["traceback"] is not None and "ValueError: kaboom" in exc["traceback"]


# ---------- endpoints ----------


def _dev_app() -> TestClient:
    return TestClient(create_app(in_memory_repositories(), start_triggers=False))


def test_dev_errors_endpoint_reports_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    client = _dev_app()
    h = {"X-Dev-User": "dev", "X-Dev-Groups": "admins"}

    # Start from a clean slate — the buffer is a process singleton.
    client.post("/api/dev/errors/clear", headers=h)

    logging.getLogger("test.endpoint").error("endpoint boom")

    body = client.get("/api/dev/errors", headers=h).json()
    assert body["total"] >= 1
    assert any(e["message"] == "endpoint boom" for e in body["errors"])

    assert client.post("/api/dev/errors/clear", headers=h).json() == {"status": "cleared"}
    assert client.get("/api/dev/errors", headers=h).json()["errors"] == []


def test_dev_routes_absent_outside_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "oidc")
    app = create_app(in_memory_repositories(), start_triggers=False)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/dev/errors" not in paths
    assert "/api/dev/errors/clear" not in paths
