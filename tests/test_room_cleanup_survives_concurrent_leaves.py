"""Emptying a room must not delete somebody else's room.

`leave_room` captured `self._rooms[project_id]` and then suspended three
times — the presence `zrem`, the broadcast, and `get_presence` — before
deciding, from that captured reference, whether to delete the key. Two things
go wrong when the last two connections leave at once:

- both calls pass the top guard, both see the shared room empty after their
  broadcasts, and the second `del self._rooms[project_id]` raises KeyError;
- worse, if a join for the same project lands between the two resumptions,
  `_get_or_create_room` puts a NEW room under that key. The stale leave still
  sees its OLD empty room, but deletes the NEW one and unsubscribes the
  project's channel while it has live connections — those users stop receiving
  broadcasts, silently, with the socket still open.

Cleanup now deletes only the room it actually emptied.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from metaseed_hub.websocket import WebSocketManager

pytestmark = pytest.mark.asyncio


class _Socket:
    """A websocket that accepts and swallows sends."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, message: Any) -> None:
        self.sent.append(message)


def _socket() -> _Socket:
    return _Socket()


async def _manager_with_two_connections() -> WebSocketManager:
    manager = WebSocketManager()
    await manager.join_room("p-1", "c-1", _socket(), "u-1", "One")
    await manager.join_room("p-1", "c-2", _socket(), "u-2", "Two")
    return manager


async def test_the_last_two_leaving_at_once_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both calls reach the cleanup with the room already empty."""
    manager = await _manager_with_two_connections()
    both_removed = asyncio.Event()

    async def _broadcast_after_both_removed(*args: Any, **kwargs: Any) -> None:
        # The suspension point the two calls interleave across: by the time
        # either resumes, both connections are gone from the shared room.
        if not manager._rooms["p-1"].connections:
            both_removed.set()
        await both_removed.wait()

    monkeypatch.setattr(manager, "broadcast_to_room", _broadcast_after_both_removed)

    results = await asyncio.gather(
        manager.leave_room("p-1", "c-1"),
        manager.leave_room("p-1", "c-2"),
        return_exceptions=True,
    )

    assert [r for r in results if isinstance(r, Exception)] == []
    assert "p-1" not in manager._rooms


async def test_a_stale_leave_does_not_delete_a_rejoined_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A join between the leave's suspension and its cleanup keeps its room."""
    manager = WebSocketManager()
    await manager.join_room("p-1", "c-1", _socket(), "u-1", "One")
    rejoined = asyncio.Event()

    async def _rejoin_during_the_broadcast(*args: Any, **kwargs: Any) -> None:
        # One-shot: the nested join broadcasts too, and re-entering here would
        # recurse instead of modelling the race.
        if rejoined.is_set():
            return
        rejoined.set()
        # The old room is empty and still under the key; a fresh join replaces
        # it while the leaving call is suspended here.
        del manager._rooms["p-1"]
        await manager.join_room("p-1", "c-2", _socket(), "u-2", "Two")

    monkeypatch.setattr(manager, "broadcast_to_room", _rejoin_during_the_broadcast)

    await manager.leave_room("p-1", "c-1")

    assert rejoined.is_set()
    assert "p-1" in manager._rooms, "the leaving connection deleted the room that replaced its own"
    assert "c-2" in manager._rooms["p-1"].connections
