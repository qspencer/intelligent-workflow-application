"""Tests for webhook HMAC verification (G2).

A webhook trigger whose config names a `secret_name` requires a GitHub-style
`X-Hub-Signature-256: sha256=<hex hmac-sha256(secret, raw_body)>` header; one
without stays on the unsigned dev path (covered in test_webhook_trigger.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.secrets import EnvSecretStore
from workflow_platform.triggers import WebhookRegistry, WebhookTrigger

SECRET_NAME = "WEBHOOK_TEST_SECRET"
SECRET = "s3cret-value"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _secured_app(
    monkeypatch: pytest.MonkeyPatch, *, seed_secret: bool = True
) -> tuple[TestClient, list[dict[str, Any]]]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTH_MODE", "dev")
    if seed_secret:
        monkeypatch.setenv(SECRET_NAME, SECRET)
    else:
        monkeypatch.delenv(SECRET_NAME, raising=False)

    registry = WebhookRegistry()
    received: list[dict[str, Any]] = []

    async def cb(payload: dict[str, Any]) -> None:
        received.append(payload)

    asyncio.run(WebhookTrigger(registry, "secure-hook", secret_name=SECRET_NAME).start(cb))
    app = create_app(
        repositories=in_memory_repositories(),
        webhook_registry=registry,
        secret_store=EnvSecretStore(),
    )
    return TestClient(app), received


def test_valid_signature_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    client, received = _secured_app(monkeypatch)
    body = json.dumps({"event": "push"}).encode()
    r = client.post(
        "/api/triggers/webhook/secure-hook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert received == [{"event": "push"}]


def test_missing_signature_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, received = _secured_app(monkeypatch)
    r = client.post("/api/triggers/webhook/secure-hook", json={"event": "push"})
    assert r.status_code == 401
    assert received == []


def test_wrong_secret_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, received = _secured_app(monkeypatch)
    body = json.dumps({"event": "push"}).encode()
    r = client.post(
        "/api/triggers/webhook/secure-hook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, "wrong-secret"),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert received == []


def test_tampered_body_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, received = _secured_app(monkeypatch)
    signed_for = json.dumps({"amount": 1}).encode()
    r = client.post(
        "/api/triggers/webhook/secure-hook",
        content=json.dumps({"amount": 9999}).encode(),
        headers={"X-Hub-Signature-256": _sign(signed_for), "Content-Type": "application/json"},
    )
    assert r.status_code == 401
    assert received == []


def test_missing_secret_fails_closed_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trigger configured for HMAC whose secret can't be loaded must refuse —
    never fall open to unsigned."""
    client, received = _secured_app(monkeypatch, seed_secret=False)
    body = json.dumps({"event": "push"}).encode()
    r = client.post(
        "/api/triggers/webhook/secure-hook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 503
    assert received == []


def test_signature_checked_before_json_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsigned garbage gets 401 (not 400) — don't parse untrusted bodies."""
    client, _ = _secured_app(monkeypatch)
    r = client.post(
        "/api/triggers/webhook/secure-hook",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
