"""WebSocket manager for real-time features."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket, WebSocketDisconnect

from metaseed_hub.config import get_settings


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
        """Connect to Redis for pub/sub."""
        settings = get_settings()
        self._redis = redis.from_url(settings.redis_url)
        self._pubsub = self._redis.pubsub()

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
        if project_id not in self._rooms:
            return

        room = self._rooms[project_id]
        message_json = json.dumps(message)

        # Publish to Redis for other instances
        if self._redis:
            channel = self._get_channel_name(project_id)
            await self._redis.publish(channel, message_json)

        # Send to local connections
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

    def get_room_presence(self, project_id: str) -> list[dict[str, Any]]:
        """Get presence information for a room.

        Args:
            project_id: Project identifier.

        Returns:
            List of users in the room.
        """
        if project_id not in self._rooms:
            return []
        return self._rooms[project_id].get_presence()

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
