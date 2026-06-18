"""WebSocket manager for real-time features."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
    connected_at: datetime = field(default_factory=datetime.now)


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


class WebSocketManager:
    """Manages WebSocket connections with Redis pub/sub for scaling."""

    def __init__(self) -> None:
        """Initialize the WebSocket manager."""
        self._rooms: dict[str, Room] = {}
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None

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

    async def _listen(self) -> None:
        """Consume published messages and deliver them to local connections.

        Runs until cancelled (on shutdown). Errors are logged and the loop
        continues so a single bad message does not stop delivery.
        """
        assert self._pubsub is not None
        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WebSocket pub/sub listener error")
                await asyncio.sleep(0.5)
                continue

            if not message or message.get("type") != "message":
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
        for conn_id, connection in room.connections.items():
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
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.aclose()

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

        # Subscribe to Redis channel for this project
        if self._pubsub:
            channel = self._get_channel_name(project_id)
            await self._pubsub.subscribe(channel)

        # Broadcast presence update
        await self.broadcast_to_room(
            project_id,
            {
                "type": "presence",
                "action": "join",
                "user_id": user_id,
                "user_name": user_name,
                "presence": room.get_presence(),
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
            # Broadcast presence update
            await self.broadcast_to_room(
                project_id,
                {
                    "type": "presence",
                    "action": "leave",
                    "user_id": connection.user_id,
                    "user_name": connection.user_name,
                    "presence": room.get_presence(),
                },
            )

        # Clean up empty rooms
        if not room.connections:
            del self._rooms[project_id]
            if self._pubsub:
                channel = self._get_channel_name(project_id)
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
        for conn_id, connection in room.connections.items():
            if conn_id == exclude_connection:
                continue
            try:
                await connection.websocket.send_text(message_json)
            except Exception:
                disconnected.append(conn_id)

        # Clean up disconnected connections
        for conn_id in disconnected:
            await self.leave_room(project_id, conn_id)

    async def send_to_connection(
        self,
        project_id: str,
        connection_id: str,
        message: dict[str, Any],
    ) -> None:
        """Send a message to a specific connection.

        Args:
            project_id: Project identifier.
            connection_id: Connection identifier.
            message: Message to send.
        """
        if project_id not in self._rooms:
            return

        room = self._rooms[project_id]
        if connection_id not in room.connections:
            return

        connection = room.connections[connection_id]
        try:
            await connection.websocket.send_text(json.dumps(message))
        except Exception:
            await self.leave_room(project_id, connection_id)

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
        connection_id = f"{user_id}:{id(websocket)}"

        await self.join_room(project_id, connection_id, websocket, user_id, user_name)

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                # Add sender information
                message["sender_id"] = user_id
                message["sender_name"] = user_name
                message["timestamp"] = datetime.now().isoformat()

                # Broadcast to room
                await self.broadcast_to_room(
                    project_id,
                    message,
                    exclude_connection=connection_id,
                )
        except WebSocketDisconnect:
            await self.leave_room(project_id, connection_id)
        except Exception:
            await self.leave_room(project_id, connection_id)


# Global manager instance
manager = WebSocketManager()


__all__ = [
    "Connection",
    "Room",
    "WebSocketManager",
    "manager",
]
