"""Tests for the WebSocket manager's Redis pub/sub fan-out.

These verify that, when Redis is configured, broadcasts are published (and
delivered via the listener) exactly once rather than both published and sent
locally, and that the listener dispatches messages to local connections while
honoring the per-message exclude.
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from metaseed_hub.websocket import Connection, Room, WebSocketManager


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


class _RecordingSessionContext:
    """Async context manager standing in for an AsyncSession from the factory."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.session = object()

    async def __aenter__(self) -> object:
        self.events.append("session-open")
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        self.events.append("session-close")


class _ClosableWebSocket(FakeWebSocket):
    """A FakeWebSocket that also records close codes."""

    def __init__(self) -> None:
        super().__init__()
        self.close_codes: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


def _websocket_endpoint():
    """Return the /ws/{project_id} endpoint function from the application."""
    from metaseed_hub import main as main_module

    for route in main_module.app.routes:
        if getattr(route, "path", "") == "/ws/{project_id}":
            return route.endpoint
    raise AssertionError("websocket route not found")


@pytest.mark.asyncio
async def test_ws_room_authorization_closes_db_session(monkeypatch):
    """The authorization DB session is closed before the connection is handled.

    The endpoint must drive the session as a context manager; the previous
    async-for/break pattern deferred the session close to garbage collection,
    leaking pool connections.
    """
    import metaseed_hub.main as main_module
    import metaseed_hub.ui.dependencies as deps_module

    events: list[str] = []
    ctx = _RecordingSessionContext(events)
    user = SimpleNamespace(keycloak_id="kc-1", name="User One")

    async def fake_verify_token(token: str):
        return user

    async def fake_get_dataset_for_user(project_id, session, token_user):
        events.append("authorized")
        assert session is ctx.session
        assert project_id == "proj-1"
        assert token_user is user
        return object()

    async def fake_handle_connection(**kwargs):
        events.append("handled")

    monkeypatch.setattr(main_module, "verify_token", fake_verify_token)
    monkeypatch.setattr(deps_module, "get_dataset_for_user", fake_get_dataset_for_user)
    monkeypatch.setattr(main_module.db, "_session_factory", lambda: ctx)
    monkeypatch.setattr(main_module.manager, "handle_connection", fake_handle_connection)

    ws = _ClosableWebSocket()
    await _websocket_endpoint()(websocket=ws, project_id="proj-1", token="tok")

    assert events == ["session-open", "authorized", "session-close", "handled"]
    assert ws.close_codes == []


@pytest.mark.asyncio
async def test_ws_room_authorization_denial_closes_db_session(monkeypatch):
    """A denied room join closes the socket with 4003 and still closes the session."""
    from fastapi import HTTPException

    import metaseed_hub.main as main_module
    import metaseed_hub.ui.dependencies as deps_module

    events: list[str] = []
    ctx = _RecordingSessionContext(events)

    async def fake_verify_token(token: str):
        return SimpleNamespace(keycloak_id="kc-1", name="User One")

    async def fake_get_dataset_for_user(project_id, session, token_user):
        raise HTTPException(status_code=403, detail="Access denied")

    monkeypatch.setattr(main_module, "verify_token", fake_verify_token)
    monkeypatch.setattr(deps_module, "get_dataset_for_user", fake_get_dataset_for_user)
    monkeypatch.setattr(main_module.db, "_session_factory", lambda: ctx)

    ws = _ClosableWebSocket()
    await _websocket_endpoint()(websocket=ws, project_id="proj-1", token="tok")

    assert events == ["session-open", "session-close"]
    assert ws.close_codes == [4003]


class RoomMutatingWebSocket(FakeWebSocket):
    """A websocket whose first send mutates the room, like a concurrent join.

    Every ``send_text`` in a broadcast loop is a suspension point where a
    concurrent ``join_room``/``leave_room`` can add or remove connections. This
    fake performs that mutation inside the send so iterating the live dict
    would raise ``RuntimeError: dictionary changed size during iteration``.
    """

    def __init__(self, room: Room) -> None:
        super().__init__()
        self._room = room
        self._mutated = False

    async def send_text(self, text: str) -> None:
        if not self._mutated:
            self._mutated = True
            self._room.add_connection(
                "conn-late",
                Connection(websocket=FakeWebSocket(), user_id="late", user_name="late"),  # type: ignore[arg-type]
            )
        await super().send_text(text)


@pytest.mark.asyncio
async def test_dispatch_local_survives_room_mutation_during_send() -> None:
    """A join landing mid-dispatch must not abort delivery to remaining peers."""
    manager = WebSocketManager()
    room = manager._get_or_create_room("proj-1")
    mutating_ws = RoomMutatingWebSocket(room)
    room.add_connection(
        "conn-1",
        Connection(websocket=mutating_ws, user_id="u1", user_name="u1"),  # type: ignore[arg-type]
    )
    ws2 = _add_connection(manager, "proj-1", "conn-2")

    envelope = json.dumps({"exclude": None, "message": {"type": "chat", "text": "hi"}})
    await manager._dispatch_local("project:proj-1:messages", envelope)

    assert len(mutating_ws.sent) == 1
    assert len(ws2.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_without_redis_survives_room_mutation_during_send() -> None:
    """The no-Redis broadcast path tolerates the same mid-send room mutation."""
    manager = WebSocketManager()
    room = manager._get_or_create_room("proj-1")
    mutating_ws = RoomMutatingWebSocket(room)
    room.add_connection(
        "conn-1",
        Connection(websocket=mutating_ws, user_id="u1", user_name="u1"),  # type: ignore[arg-type]
    )
    ws2 = _add_connection(manager, "proj-1", "conn-2")

    await manager.broadcast_to_room("proj-1", {"type": "chat", "text": "hi"})

    assert len(mutating_ws.sent) == 1
    assert len(ws2.sent) == 1


class ScriptedWebSocket(FakeWebSocket):
    """A websocket that yields scripted frames, then disconnects."""

    def __init__(self, frames: list[str]) -> None:
        super().__init__()
        self._frames = iter(frames)

    async def receive_text(self) -> str:
        try:
            return next(self._frames)
        except StopIteration:
            raise WebSocketDisconnect(1000) from None


async def _run_connection_and_get_chat_envelope(
    manager: WebSocketManager, user_id: str
) -> dict[str, object]:
    ws = ScriptedWebSocket([json.dumps({"type": "chat", "text": "hi"})])
    await manager.handle_connection(ws, "proj-1", user_id, "User")  # type: ignore[arg-type]
    chat_envelopes = [
        envelope
        for _, payload in manager._redis.published  # type: ignore[attr-defined]
        if (envelope := json.loads(payload))["message"]["type"] == "chat"
    ]
    assert len(chat_envelopes) == 1
    return chat_envelopes[0]  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_handle_connection_ids_are_uuid_based_and_unique() -> None:
    """Connection IDs travel through Redis cluster-wide, so they must not rely on ``id()``."""
    envelope1 = await _run_connection_and_get_chat_envelope(_manager_with_redis(), "user-1")
    envelope2 = await _run_connection_and_get_chat_envelope(_manager_with_redis(), "user-1")

    assert re.fullmatch(r"user-1:[0-9a-f]{32}", str(envelope1["exclude"]))
    assert envelope1["exclude"] != envelope2["exclude"]


@pytest.mark.asyncio
async def test_handle_connection_timestamps_are_utc_aware() -> None:
    """Broadcast timestamps carry a UTC offset, matching the HTTP side."""
    envelope = await _run_connection_and_get_chat_envelope(_manager_with_redis(), "user-1")

    message = envelope["message"]
    assert isinstance(message, dict)
    timestamp = datetime.fromisoformat(message["timestamp"])
    assert timestamp.utcoffset() is not None
    assert timestamp.tzinfo == UTC


def _manager_with_redis() -> WebSocketManager:
    manager = WebSocketManager()
    manager._redis = FakeRedis()  # type: ignore[assignment]
    return manager


def test_connection_connected_at_is_utc_aware() -> None:
    """Presence ``connected_at`` values are timezone-aware UTC."""
    connection = Connection(websocket=FakeWebSocket(), user_id="u1", user_name="U1")  # type: ignore[arg-type]
    assert connection.connected_at.tzinfo == UTC
