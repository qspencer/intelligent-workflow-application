"""Tests for the WebSocket event stream."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.events import EventBus
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories


@pytest.fixture
def ws_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, EventBus]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    bus = EventBus()
    app = create_app(repositories=in_memory_repositories(), events=bus)
    return TestClient(app), bus


def test_websocket_streams_published_events(
    ws_app: tuple[TestClient, EventBus],
) -> None:
    client, bus = ws_app
    with client.websocket_connect("/ws/events?user=alice&groups=admins") as websocket:
        # publish from the bus while the socket is connected
        async def _publish() -> None:
            await bus.publish({"action": "workflow_started", "id": "abc"})

        # TestClient's websocket_connect is sync; run the coroutine via a fresh loop.
        asyncio.new_event_loop().run_until_complete(_publish())

        msg = websocket.receive_json()
        assert msg == {"action": "workflow_started", "id": "abc"}


def test_websocket_handler_exits_on_disconnect_with_no_events(
    ws_app: tuple[TestClient, EventBus],
) -> None:
    """Regression: the handler used to block on queue.get() and only notice a
    dead peer at the next send — on a quiet bus, never. The still-alive task
    hung uvicorn's --reload/shutdown ("Waiting for background tasks to
    complete") whenever a dashboard tab was open. Disconnecting with ZERO
    events published must release the handler (observable via unsubscribe)."""
    import time

    client, bus = ws_app
    with client.websocket_connect("/ws/events?user=alice&groups=admins"):
        assert len(bus._subscribers) == 1
    # Context exit sends websocket.disconnect; the handler must notice it
    # promptly (no event ever flowed) and unsubscribe on the way out.
    deadline = 50
    while len(bus._subscribers) > 0 and deadline > 0:
        time.sleep(0.05)
        deadline -= 1
    assert len(bus._subscribers) == 0


def test_websocket_rejects_unauthenticated_connection(
    ws_app: tuple[TestClient, EventBus],
) -> None:
    from starlette.websockets import WebSocketDisconnect

    client, _ = ws_app
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect("/ws/events") as ws,
    ):
        ws.receive_json()
    # 1008 = WS_1008_POLICY_VIOLATION
    assert excinfo.value.code == 1008


async def test_event_bus_publishes_to_multiple_subscribers() -> None:
    bus = EventBus()
    a = bus.subscribe()
    b = bus.subscribe()
    await bus.publish({"x": 1})
    assert a.get_nowait() == {"x": 1}
    assert b.get_nowait() == {"x": 1}


async def test_event_bus_drops_when_subscriber_full() -> None:
    bus = EventBus(queue_size=1)
    sub = bus.subscribe()
    await bus.publish({"first": True})
    await bus.publish({"second": True})  # second should be dropped silently
    assert sub.get_nowait() == {"first": True}
    assert sub.qsize() == 0


async def test_engine_publishes_audit_to_event_bus() -> None:
    """The executor should mirror every audit append to the event bus."""
    from tests._bedrock_fakes import FakeBedrock
    from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    bus = EventBus()
    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for event in bus.stream():
            received.append(event)
            if event.get("action") == "workflow_completed":
                return

    consumer = asyncio.create_task(consume())
    # Yield once to let the subscriber register before publish.
    await asyncio.sleep(0)

    fns = FunctionRegistry()

    async def noop(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {}

    fns.register("noop", noop)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        events=bus,
    )
    await engine.run(definition)
    await asyncio.wait_for(consumer, timeout=2.0)

    actions = [e["action"] for e in received]
    assert actions[0] == "workflow_started"
    assert actions[-1] == "workflow_completed"
