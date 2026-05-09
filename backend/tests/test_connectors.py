"""Tests for the Connector framework: base, registry, webhook, S3."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from workflow_platform.connectors import (
    Connector,
    ConnectorRegistry,
    S3Connector,
    WebhookConnector,
)
from workflow_platform.secrets import EnvSecretStore

# --- Connector base ---


async def test_default_connector_send_and_query_raise() -> None:
    class Stub(Connector):
        type = "stub"

        async def authenticate(self) -> None:
            return None

        async def health_check(self) -> bool:
            return True

    s = Stub()
    with pytest.raises(NotImplementedError):
        await s.send({})
    with pytest.raises(NotImplementedError):
        await s.query({})
    # trigger methods default to no-op
    assert await s.trigger_poll() == []

    async def _no_op(_: dict[str, Any]) -> None:
        return None

    # Default trigger_listen returns None; calling it must not raise.
    await s.trigger_listen(_no_op)


# --- Registry ---


def test_registry_register_get_and_duplicates() -> None:
    registry = ConnectorRegistry()
    connector = WebhookConnector(send_url="https://example.com")
    registry.register("hook", connector)
    assert registry.get("hook") is connector
    assert "hook" in registry
    assert registry.names() == ["hook"]
    with pytest.raises(ValueError, match="already registered"):
        registry.register("hook", connector)


# --- WebhookConnector ---


async def test_webhook_send_posts_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(202, json={"ok": True})

    transport = httpx.MockTransport(handler)
    # Patch httpx.AsyncClient to use our mock transport.
    real_async_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: real_async_client(transport=transport)  # type: ignore[misc,assignment]
    try:
        connector = WebhookConnector(send_url="https://hooks.example/notify")
        result = await connector.send({"hello": "world"})
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc]

    assert captured["method"] == "POST"
    assert captured["url"] == "https://hooks.example/notify"
    assert b"world" in captured["body"]
    assert result == {"status_code": 202, "body": {"ok": True}}


async def test_webhook_query_sends_get_with_params() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"items": [1, 2]})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: real(transport=transport)  # type: ignore[misc,assignment]
    try:
        connector = WebhookConnector(query_url="https://api.example/data")
        result = await connector.query({"limit": "5"})
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]

    assert "limit=5" in captured["url"]
    assert result == {"status_code": 200, "body": {"items": [1, 2]}}


async def test_webhook_health_check_returns_false_on_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: real(transport=transport)  # type: ignore[misc,assignment]
    try:
        connector = WebhookConnector(health_url="https://down.example")
        assert await connector.health_check() is False
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]


async def test_webhook_authenticate_validates_secret_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOOK_AUTH", "X-Token: abc")
    connector = WebhookConnector(
        send_url="https://x", auth_header_secret="HOOK_AUTH", secret_store=EnvSecretStore()
    )
    await connector.authenticate()  # no exception means OK


async def test_webhook_authenticate_raises_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOOK_AUTH", raising=False)
    connector = WebhookConnector(
        send_url="https://x", auth_header_secret="HOOK_AUTH", secret_store=EnvSecretStore()
    )
    with pytest.raises(RuntimeError, match="missing"):
        await connector.authenticate()


# --- S3Connector ---


def _fake_s3() -> Any:
    client = MagicMock()
    client._objects = {}
    return client


async def test_s3_send_calls_put_object() -> None:
    client = _fake_s3()
    connector = S3Connector(bucket="my-bucket", client=client)
    result = await connector.send({"key": "out/x.txt", "body": "hello"})
    client.put_object.assert_called_once()
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "out/x.txt"
    assert kwargs["Body"] == b"hello"
    assert result == {"bucket": "my-bucket", "key": "out/x.txt", "bytes": 5}


async def test_s3_send_with_content_type() -> None:
    client = _fake_s3()
    connector = S3Connector(bucket="b", client=client)
    await connector.send({"key": "k", "body": b"data", "content_type": "application/json"})
    assert client.put_object.call_args.kwargs["ContentType"] == "application/json"


async def test_s3_query_list_returns_keys() -> None:
    client = _fake_s3()
    client.list_objects_v2.return_value = {
        "Contents": [{"Key": "a.txt"}, {"Key": "b.txt"}],
    }
    connector = S3Connector(bucket="b", client=client)
    result = await connector.query({"kind": "list", "prefix": "in/"})
    client.list_objects_v2.assert_called_once_with(Bucket="b", Prefix="in/")
    assert result == {"keys": ["a.txt", "b.txt"]}


async def test_s3_query_get_reads_body() -> None:
    client = _fake_s3()
    body = MagicMock()
    body.read.return_value = b"the body"
    client.get_object.return_value = {"Body": body}
    connector = S3Connector(bucket="b", client=client)
    result = await connector.query({"kind": "get", "key": "x"})
    assert result == {"key": "x", "body": "the body"}


async def test_s3_trigger_poll_emits_only_new_keys() -> None:
    client = _fake_s3()
    client.list_objects_v2.return_value = {
        "Contents": [{"Key": "a", "Size": 1}, {"Key": "b", "Size": 2}]
    }
    connector = S3Connector(bucket="b", client=client)

    first = await connector.trigger_poll()
    assert {e["key"] for e in first} == {"a", "b"}

    # No new objects → empty events on the next poll.
    second = await connector.trigger_poll()
    assert second == []

    # Add a new key.
    client.list_objects_v2.return_value = {
        "Contents": [{"Key": "a", "Size": 1}, {"Key": "b", "Size": 2}, {"Key": "c", "Size": 3}]
    }
    third = await connector.trigger_poll()
    assert [e["key"] for e in third] == ["c"]


async def test_s3_health_check_false_when_head_bucket_raises() -> None:
    client = _fake_s3()
    client.head_bucket.side_effect = RuntimeError("nope")
    connector = S3Connector(bucket="b", client=client)
    assert await connector.health_check() is False


async def test_s3_health_check_true_on_success() -> None:
    client = _fake_s3()
    client.head_bucket.return_value = {}
    connector = S3Connector(bucket="b", client=client)
    assert await connector.health_check() is True
