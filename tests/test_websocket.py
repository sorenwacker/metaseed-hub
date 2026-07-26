"""Tests for the WebSocket manager's Redis pub/sub fan-out.

These verify that, when Redis is configured, broadcasts are published (and
delivered via the listener) exactly once rather than both published and sent
locally, and that the listener dispatches messages to local connections while
honoring the per-message exclude.
"""

import asyncio
import json

import pytest

from metaseed_hub.websocket import Connection, WebSocketManager


class FakeWebSocket:
    """Records messages sent to a connection."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


class RecordingPubSub:
    """Records subscribe/unsubscribe calls on the shared PubSub connection."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)


class NeverSubscribedPubSub:
    """A PubSub with no subscriptions whose read would raise if ever called."""

    def __init__(self) -> None:
        self.subscribed = False
        self.get_message_calls = 0

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float = 0
    ) -> None:
        # Mirrors redis-py: reading before any subscribe raises.
        self.get_message_calls += 1
        raise RuntimeError("pubsub connection not set")


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
async def test_listen_skips_read_until_subscribed() -> None:
    """The listener does not read the PubSub while there are no subscriptions.

    Reading before the first subscribe raises ``RuntimeError`` in redis-py; the
    listener must skip the read (not spin on the error) until a room subscribes.
    """
    manager = WebSocketManager()
    manager._pubsub = NeverSubscribedPubSub()  # type: ignore[assignment]

    task = asyncio.create_task(manager._listen())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert manager._pubsub.get_message_calls == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_join_room_subscribe_serialized_behind_pubsub_lock() -> None:
    """Per-room subscribe is gated by the shared PubSub lock the listener holds.

    While the lock is held, ``join_room`` cannot run its ``subscribe`` on the
    shared connection; once released the subscription is recorded. This pins the
    serialization that prevents the listener's read from interleaving with
    subscribe/unsubscribe and corrupting the RESP stream.
    """
    manager = WebSocketManager()
    manager._redis = FakeRedis()  # type: ignore[assignment]
    manager._pubsub = RecordingPubSub()  # type: ignore[assignment]
    ws = FakeWebSocket()

    await manager._pubsub_lock.acquire()
    task = asyncio.create_task(
        manager.join_room("proj-1", "conn-1", ws, "user-1", "User One")  # type: ignore[arg-type]
    )
    # Let join_room run as far as it can; it must block on the held lock.
    await asyncio.sleep(0.01)
    assert manager._pubsub.subscribed == []  # type: ignore[attr-defined]

    manager._pubsub_lock.release()
    await task
    assert manager._pubsub.subscribed == ["project:proj-1:messages"]  # type: ignore[attr-defined]


class SlowReadPubSub:
    """A PubSub whose read holds the caller for one full ``timeout`` period.

    Models redis-py: ``get_message`` blocks up to ``timeout`` seconds. The
    listener runs this read while holding ``_pubsub_lock``, so the read's
    duration bounds how long a concurrent join/leave waits for the lock.
    """

    def __init__(self) -> None:
        self.subscribed = False
        self.subscribes: list[str] = []
        self.read_started = asyncio.Event()

    async def subscribe(self, channel: str) -> None:
        self.subscribes.append(channel)
        self.subscribed = True

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float = 0
    ) -> None:
        self.read_started.set()
        await asyncio.sleep(timeout)
        return None


@pytest.mark.asyncio
async def test_join_not_stalled_for_a_full_second_behind_listener_read() -> None:
    """A join contending with an in-progress listener read must not stall ~1s.

    The listener holds ``_pubsub_lock`` for the duration of each ``get_message``
    read. If that read blocks for a full second, every join/leave that needs the
    lock stalls that long. The read timeout is bounded so a join arriving mid-read
    waits only a fraction of a second.
    """
    manager = WebSocketManager()
    manager._redis = FakeRedis()  # type: ignore[assignment]
    pubsub = SlowReadPubSub()
    manager._pubsub = pubsub  # type: ignore[assignment]

    # First join before the listener runs: records a subscription so the
    # listener's next iteration actually performs a (lock-holding) read.
    await manager.join_room("proj-1", "conn-1", FakeWebSocket(), "u1", "U1")  # type: ignore[arg-type]

    task = asyncio.create_task(manager._listen())
    # Wait until the listener is inside get_message, holding the lock.
    await asyncio.wait_for(pubsub.read_started.wait(), timeout=2.0)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await manager.join_room("proj-2", "conn-2", FakeWebSocket(), "u2", "U2")  # type: ignore[arg-type]
    elapsed = loop.time() - start

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "project:proj-2:messages" in pubsub.subscribes
    # A full-second lock hold (the bug) would push this well past 0.5s.
    assert elapsed < 0.5


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
