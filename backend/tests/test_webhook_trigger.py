"""Tests for the webhook trigger registry + API integration."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.triggers import WebhookRegistry, WebhookTrigger

# --- registry / trigger primitives ---


async def test_webhook_trigger_routes_callback_through_registry() -> None:
    registry = WebhookRegistry()
    trigger = WebhookTrigger(registry, trigger_id="my-hook")
    received: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        received.append(payload)

    await trigger.start(on_event)
    assert registry.is_registered("my-hook")

    fired = await registry.fire("my-hook", {"a": 1})
    assert fired
    assert received == [{"a": 1}]

    await trigger.stop()
    assert not registry.is_registered("my-hook")


async def test_webhook_registry_returns_false_for_unknown_id() -> None:
    registry = WebhookRegistry()
    assert await registry.fire("nope", {"x": 1}) is False


async def test_webhook_registry_rejects_duplicate_registration() -> None:
    registry = WebhookRegistry()
    trigger = WebhookTrigger(registry, "x")

    async def cb(_: dict[str, Any]) -> None:
        return None

    await trigger.start(cb)
    with pytest.raises(ValueError, match="already registered"):
        await trigger.start(cb)
    await trigger.stop()


# --- end-to-end through the API ---


def test_webhook_endpoint_fires_registered_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTH_MODE", "dev")
    registry = WebhookRegistry()
    received: list[dict[str, Any]] = []

    async def cb(payload: dict[str, Any]) -> None:
        received.append(payload)

    import asyncio

    asyncio.run(WebhookTrigger(registry, "my-hook").start(cb))

    app = create_app(
        repositories=in_memory_repositories(),
        webhook_registry=registry,
    )
    client = TestClient(app)
    # No auth header — webhook endpoints are exempt from the user auth middleware.
    response = client.post("/api/triggers/webhook/my-hook", json={"file_path": "/x.pdf"})
    assert response.status_code == 200
    assert response.json() == {"status": "fired", "trigger_id": "my-hook"}
    assert received == [{"file_path": "/x.pdf"}]


def test_webhook_endpoint_404_for_unknown_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTH_MODE", "dev")
    app = create_app(
        repositories=in_memory_repositories(),
        webhook_registry=WebhookRegistry(),
    )
    client = TestClient(app)
    response = client.post("/api/triggers/webhook/nope", json={})
    assert response.status_code == 404
