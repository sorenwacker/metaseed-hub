"""WebSocket manager for real-time features."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from fastapi import WebSocket, WebSocketDisconnect

from metaseed_hub.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    """Represents a WebSocket connection."""

    websocket: WebSocket
    user_id: str
    user_name: str
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Room:
    """Represents a room for project-based messaging."""

    project_id: str
    connections: dict[str, Connection] = field(default_factory=dict)

    def add_connection(self, connection_id: str, connection: Connection) -> None:
        """Add a connection to the room."""
        self.connections[connection_id] = connection

    def remove_connection(self, connection_id: str) -> Connection | None:
        """Remove a connection from the room."""
        return self.connections.pop(connection_id, None)

    def get_presence(self) -> list[dict[str, Any]]:
        """Get list of users currently in the room."""
        return [
            {
                "user_id": conn.user_id,
                "user_name": conn.user_name,
                "connected_at": conn.connected_at.isoformat(),
            }
            for conn in self.connections.values()
        ]


SERVER_MESSAGE_TYPES: frozenset[str] = frozenset({"presence"})

#: How often each instance re-stamps its connections into the shared presence
#: set. An entry older than three beats is a process that stopped refreshing —
#: crashed or partitioned — and its users age out of presence without any
#: cleanup handshake.
PRESENCE_HEARTBEAT_SECONDS = 30
PRESENCE_STALE_AFTER_SECONDS = PRESENCE_HEARTBEAT_SECONDS * 3
"""Message types only the server may originate.

``presence`` frames are how clients learn who is in the room; a client frame
claiming the type would be relayed with a stamped ``sender_id`` but still
rendered as the room's presence list by every other client. Reserved types are
dropped before broadcast rather than trusted because they arrived on a socket.
"""


class WebSocketManager:
    """Manages WebSocket connections, with Redis carrying the shared state.

    MESSAGES cross instances via pub/sub. PRESENCE is a per-room Redis sorted
    set scored by a heartbeat timestamp: every instance stamps its own
    connections in, reads the whole set back, and a process that stops
    refreshing ages out after three missed beats. Without Redis (single
    process), presence falls back to the local connection list, which is then
    also the whole truth.
    """

    # Blocking read timeout for the listener's get_message call. The listener
    # holds _pubsub_lock for the duration of each read, so every join/leave
    # (which needs the lock to subscribe/unsubscribe) stalls up to this long.
    # Keep it short so room churn stays responsive; the cost is a slightly
    # busier idle loop.
    _LISTEN_READ_TIMEOUT = 0.1

    def __init__(self) -> None:
        """Initialize the WebSocket manager."""
        self._rooms: dict[str, Room] = {}
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        # Serializes access to the shared PubSub connection. The listener's
        # get_message read and the per-room subscribe/unsubscribe writes run on
        # the same connection; without this lock they interleave and corrupt the
        # RESP stream (dropped/misrouted messages, parse errors).
        self._pubsub_lock = asyncio.Lock()

    async def connect_redis(self) -> None:
        """Connect to Redis for pub/sub and start the message listener.

        The listener consumes messages published to subscribed project channels
        and delivers them to this instance's local connections, which is how
        broadcasts fan out across multiple application instances.
        """
        settings = get_settings()
        self._redis = redis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        self._pubsub = self._redis.pubsub()
        self._listener_task = asyncio.create_task(self._listen())
        self._heartbeat_task = asyncio.create_task(self._presence_heartbeat())

    async def _listen(self) -> None:
        """Consume published messages and deliver them to local connections.

        Runs until cancelled (on shutdown). Errors are logged and the loop
        continues so a single bad message does not stop delivery.
        """
        assert self._pubsub is not None
        while True:
            try:
                async with self._pubsub_lock:
                    # get_message raises until the PubSub has a connection, which
                    # only happens once the first room subscribes. Skip the read
                    # while there are no subscriptions rather than spinning on the
                    # resulting RuntimeError.
                    subscribed = self._pubsub.subscribed
                    message = (
                        await self._pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=self._LISTEN_READ_TIMEOUT,
                        )
                        if subscribed
                        else None
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WebSocket pub/sub listener error")
                await asyncio.sleep(0.5)
                continue

            if not message or message.get("type") != "message":
                if not subscribed:
                    # Idle with no subscriptions: yield to avoid a busy loop.
                    await asyncio.sleep(0.5)
                continue

            try:
                await self._dispatch_local(message["channel"], message["data"])
            except Exception:
                logger.exception("Failed to dispatch pub/sub message")

    async def _dispatch_local(self, channel: str | bytes, payload: str | bytes) -> None:
        """Deliver a published message to this instance's local connections.

        Args:
            channel: Redis channel the message arrived on.
            payload: JSON envelope of ``{"exclude": connection_id|None, "message": {...}}``.
        """
        if isinstance(channel, bytes):
            channel = channel.decode()
        if isinstance(payload, bytes):
            payload = payload.decode()

        project_id = channel.split(":", 2)[1]
        if project_id not in self._rooms:
            return

        envelope = json.loads(payload)
        exclude_connection = envelope.get("exclude")
        message_json = json.dumps(envelope["message"])

        room = self._rooms[project_id]
        disconnected: list[str] = []
        # Snapshot the connections: each send is a suspension point, and a
        # concurrent join/leave mutating the dict mid-iteration would raise
        # RuntimeError and drop delivery to the remaining recipients.
        for conn_id, connection in list(room.connections.items()):
            if conn_id == exclude_connection:
                continue
            try:
                await connection.websocket.send_text(message_json)
            except Exception:
                disconnected.append(conn_id)

        for conn_id in disconnected:
            await self.leave_room(project_id, conn_id)

    async def disconnect_redis(self) -> None:
        """Disconnect from Redis."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.aclose()  # type: ignore[no-untyped-call]
        if self._redis:
            await self._redis.aclose()

    def _presence_key(self, project_id: str) -> str:
        """Redis key of the room's shared presence set."""
        return f"project:{project_id}:presence"

    @staticmethod
    def _presence_member(connection_id: str, connection: Connection) -> str:
        """The stable JSON identity of one connection in the shared set.

        ``sort_keys`` matters: the heartbeat re-adds the SAME member with a
        fresh score, and member equality is byte equality.
        """
        return json.dumps(
            {
                "connection_id": connection_id,
                "user_id": connection.user_id,
                "user_name": connection.user_name,
                "connected_at": connection.connected_at.isoformat(),
            },
            sort_keys=True,
        )

    async def get_presence(self, project_id: str) -> list[dict[str, Any]]:
        """Everyone in the room, across every instance.

        Reads the shared sorted set after dropping entries older than three
        heartbeats. Without Redis the local room is the whole truth.
        """
        if self._redis is None:
            room = self._rooms.get(project_id)
            return room.get_presence() if room else []

        key = self._presence_key(project_id)
        now = datetime.now(UTC).timestamp()
        await self._redis.zremrangebyscore(key, "-inf", now - PRESENCE_STALE_AFTER_SECONDS)
        members = await self._redis.zrange(key, 0, -1)
        presence = []
        for raw in members:
            try:
                entry = json.loads(raw)
            except (TypeError, ValueError):
                continue
            presence.append(
                {
                    "user_id": entry.get("user_id"),
                    "user_name": entry.get("user_name"),
                    "connected_at": entry.get("connected_at"),
                }
            )
        return presence

    async def _stamp_presence(self, project_id: str, room: Room) -> None:
        """Write this instance's connections into the shared set, fresh-scored."""
        if self._redis is None or not room.connections:
            return
        now = datetime.now(UTC).timestamp()
        mapping = {
            self._presence_member(connection_id, connection): now
            for connection_id, connection in room.connections.items()
        }
        await self._redis.zadd(self._presence_key(project_id), mapping)

    async def _presence_heartbeat(self) -> None:
        """Re-stamp every local connection so this instance's entries stay live.

        Runs until cancelled; an instance that dies simply stops, and its
        entries age out at the read side.
        """
        while True:
            try:
                await asyncio.sleep(PRESENCE_HEARTBEAT_SECONDS)
                for project_id, room in list(self._rooms.items()):
                    await self._stamp_presence(project_id, room)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Presence heartbeat error")

    def _get_or_create_room(self, project_id: str) -> Room:
        """Get or create a room for a project."""
        if project_id not in self._rooms:
            self._rooms[project_id] = Room(project_id=project_id)
        return self._rooms[project_id]

    def _get_channel_name(self, project_id: str) -> str:
        """Get Redis channel name for a project."""
        return f"project:{project_id}:messages"

    async def join_room(
        self,
        project_id: str,
        connection_id: str,
        websocket: WebSocket,
        user_id: str,
        user_name: str,
    ) -> None:
        """Add a connection to a project room.

        Args:
            project_id: Project identifier.
            connection_id: Unique connection identifier.
            websocket: WebSocket connection.
            user_id: User identifier.
            user_name: User display name.
        """
        await websocket.accept()

        room = self._get_or_create_room(project_id)
        connection = Connection(
            websocket=websocket,
            user_id=user_id,
            user_name=user_name,
        )
        room.add_connection(connection_id, connection)

        # Into the SHARED set before the broadcast, so the presence payload
        # every instance renders already includes the newcomer.
        if self._redis is not None:
            now = datetime.now(UTC).timestamp()
            await self._redis.zadd(
                self._presence_key(project_id),
                {self._presence_member(connection_id, connection): now},
            )

        # Subscribe to Redis channel for this project
        if self._pubsub:
            channel = self._get_channel_name(project_id)
            async with self._pubsub_lock:
                await self._pubsub.subscribe(channel)

        # Broadcast presence update
        await self.broadcast_to_room(
            project_id,
            {
                "type": "presence",
                "action": "join",
                "user_id": user_id,
                "user_name": user_name,
                "presence": await self.get_presence(project_id),
            },
        )

    async def leave_room(self, project_id: str, connection_id: str) -> None:
        """Remove a connection from a project room.

        Args:
            project_id: Project identifier.
            connection_id: Connection identifier.
        """
        if project_id not in self._rooms:
            return

        room = self._rooms[project_id]
        connection = room.remove_connection(connection_id)

        if connection:
            # Out of the SHARED set before the broadcast, mirroring join.
            if self._redis is not None:
                await self._redis.zrem(
                    self._presence_key(project_id),
                    self._presence_member(connection_id, connection),
                )
            # Broadcast presence update
            await self.broadcast_to_room(
                project_id,
                {
                    "type": "presence",
                    "action": "leave",
                    "user_id": connection.user_id,
                    "user_name": connection.user_name,
                    "presence": await self.get_presence(project_id),
                },
            )

        # Clean up empty rooms. `room` was captured before three suspension
        # points, so by now the key may hold a different room (a join arrived)
        # or none at all (a concurrent leave got here first). Delete only the
        # room this call actually emptied: deleting by key alone unsubscribes a
        # live room's channel, and its users go silently undelivered.
        if not room.connections and self._rooms.get(project_id) is room:
            del self._rooms[project_id]
            if self._pubsub:
                channel = self._get_channel_name(project_id)
                async with self._pubsub_lock:
                    await self._pubsub.unsubscribe(channel)

    async def broadcast_to_room(
        self,
        project_id: str,
        message: dict[str, Any],
        exclude_connection: str | None = None,
    ) -> None:
        """Broadcast a message to all connections in a room.

        Args:
            project_id: Project identifier.
            message: Message to broadcast.
            exclude_connection: Optional connection ID to exclude from broadcast.
        """
        if self._redis:
            # Route delivery through Redis. This instance is subscribed to the
            # channel and receives its own message back via the listener, which
            # then delivers it locally. Sending locally here as well would
            # duplicate every message for users on the publishing instance.
            channel = self._get_channel_name(project_id)
            envelope = json.dumps({"exclude": exclude_connection, "message": message})
            await self._redis.publish(channel, envelope)
            return

        # No Redis configured: deliver directly to local connections.
        if project_id not in self._rooms:
            return

        room = self._rooms[project_id]
        message_json = json.dumps(message)
        disconnected: list[str] = []
        # Snapshot the connections; see _dispatch_local for why.
        for conn_id, connection in list(room.connections.items()):
            if conn_id == exclude_connection:
                continue
            try:
                await connection.websocket.send_text(message_json)
            except Exception:
                disconnected.append(conn_id)

        # Clean up disconnected connections
        for conn_id in disconnected:
            await self.leave_room(project_id, conn_id)

    async def handle_connection(
        self,
        websocket: WebSocket,
        project_id: str,
        user_id: str,
        user_name: str,
    ) -> None:
        """Handle a WebSocket connection lifecycle.

        Args:
            websocket: WebSocket connection.
            project_id: Project identifier.
            user_id: User identifier.
            user_name: User display name.
        """
        # The ID travels through Redis as the broadcast exclude marker, so it
        # must be unique across app instances, not just within this process.
        connection_id = f"{user_id}:{uuid4().hex}"

        await self.join_room(project_id, connection_id, websocket, user_id, user_name)

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                if message.get("type") in SERVER_MESSAGE_TYPES:
                    # A client claiming a server-originated type is forging
                    # state the other clients render. Dropped, not relayed.
                    logger.warning(
                        "dropping client frame claiming server type %r from %s",
                        message.get("type"),
                        user_id,
                    )
                    continue

                # Add sender information
                message["sender_id"] = user_id
                message["sender_name"] = user_name
                message["timestamp"] = datetime.now(UTC).isoformat()

                # Broadcast to room
                await self.broadcast_to_room(
                    project_id,
                    message,
                    exclude_connection=connection_id,
                )
        except WebSocketDisconnect:
            await self.leave_room(project_id, connection_id)
        except Exception:
            # Log unexpected errors (e.g. a malformed JSON frame) instead of
            # silently dropping the connection like a normal disconnect.
            logger.exception("Unexpected error in WebSocket connection handler")
            await self.leave_room(project_id, connection_id)


# Global manager instance
manager = WebSocketManager()


__all__ = [
    "Connection",
    "Room",
    "WebSocketManager",
    "manager",
]
