"""Tests for the WebSocket manager's Redis pub/sub fan-out.

These verify that, when Redis is configured, broadcasts are published (and
delivered via the listener) exactly once rather than both published and sent
locally, and that the listener dispatches messages to local connections while
honoring the per-message exclude.
"""

import json

import pytest

from metaseed_hub.websocket import Connection, WebSocketManager


class FakeWebSocket:
    """Records messages sent to a connection."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


class FakeRedis:
    """Records publishes to Redis channels."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def _add_connection(manager: WebSocketManager, project_id: str, conn_id: str) -> FakeWebSocket:
    ws = FakeWebSocket()
    room = manager._get_or_create_room(project_id)
    room.add_connection(conn_id, Connection(websocket=ws, user_id=conn_id, user_name=conn_id))
    return ws


@pytest.mark.asyncio
async def test_broadcast_with_redis_publishes_once_and_does_not_send_locally() -> None:
    """With Redis, broadcast publishes an envelope and does not also send locally."""
    manager = WebSocketManager()
    manager._redis = FakeRedis()  # type: ignore[assignment]
    ws = _add_connection(manager, "proj-1", "conn-1")

    await manager.broadcast_to_room("proj-1", {"type": "chat", "text": "hi"})

    # Published exactly one envelope wrapping the message.
    assert len(manager._redis.published) == 1  # type: ignore[attr-defined]
    channel, payload = manager._redis.published[0]  # type: ignore[attr-defined]
    assert channel == "project:proj-1:messages"
    envelope = json.loads(payload)
    assert envelope["message"] == {"type": "chat", "text": "hi"}
    # Not delivered locally here (the listener does that on loopback).
    assert ws.sent == []


@pytest.mark.asyncio
async def test_broadcast_without_redis_sends_locally() -> None:
    """Without Redis, broadcast delivers directly to local connections."""
    manager = WebSocketManager()
    ws = _add_connection(manager, "proj-1", "conn-1")

    await manager.broadcast_to_room("proj-1", {"type": "chat", "text": "hi"})

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "chat", "text": "hi"}


@pytest.mark.asyncio
async def test_dispatch_local_delivers_excluding_sender() -> None:
    """The listener dispatch delivers to all local connections except the excluded one."""
    manager = WebSocketManager()
    ws1 = _add_connection(manager, "proj-1", "conn-1")
    ws2 = _add_connection(manager, "proj-1", "conn-2")

    envelope = json.dumps({"exclude": "conn-1", "message": {"type": "chat", "text": "hi"}})
    await manager._dispatch_local("project:proj-1:messages", envelope)

    assert ws1.sent == []
    assert len(ws2.sent) == 1
    assert json.loads(ws2.sent[0]) == {"type": "chat", "text": "hi"}


@pytest.mark.asyncio
async def test_dispatch_local_unknown_project_is_noop() -> None:
    """Dispatching to a project with no local room does nothing."""
    manager = WebSocketManager()
    envelope = json.dumps({"exclude": None, "message": {"type": "chat"}})
    # Should not raise.
    await manager._dispatch_local("project:absent:messages", envelope)


@pytest.mark.asyncio
async def test_broadcast_roundtrip_through_dispatch_delivers_once() -> None:
    """A published broadcast, when looped back through dispatch, reaches a peer once."""
    manager = WebSocketManager()
    manager._redis = FakeRedis()  # type: ignore[assignment]
    sender_ws = _add_connection(manager, "proj-1", "conn-sender")
    peer_ws = _add_connection(manager, "proj-1", "conn-peer")

    # handle_connection excludes the sender's own connection.
    await manager.broadcast_to_room(
        "proj-1", {"type": "chat", "text": "hi"}, exclude_connection="conn-sender"
    )
    # Simulate the listener receiving the published envelope back.
    _, payload = manager._redis.published[0]  # type: ignore[attr-defined]
    await manager._dispatch_local("project:proj-1:messages", payload)

    assert sender_ws.sent == []  # excluded
    assert len(peer_ws.sent) == 1  # delivered exactly once
