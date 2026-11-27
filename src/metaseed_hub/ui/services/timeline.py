"""Editing sessions: a dataset's saves grouped for the History tab.

Every save records a version, so a morning's editing is dozens of rows. Saves
by one person no more than :data:`SESSION_GAP` apart form one session. Sessions
are computed when the timeline is shown; nothing is stored.

See docs/datasets/versions.md, *The timeline*.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

#: The longest pause between two saves by one person that still counts as one session.
SESSION_GAP = timedelta(minutes=30)


@dataclass
class EditingSession:
    """Consecutive saves by one author; ``versions`` is newest first."""

    versions: list[Any] = field(default_factory=list)

    @property
    def first(self) -> Any:
        """The earliest save of the session."""
        return self.versions[-1]

    @property
    def last(self) -> Any:
        """The latest save of the session."""
        return self.versions[0]

    @property
    def named(self) -> list[Any]:
        """The versions in this session that were given a name."""
        return [v for v in self.versions if v.name]


def editing_sessions(versions: Iterable[Any]) -> list[EditingSession]:
    """Group versions into editing sessions, newest session first.

    Args:
        versions: Objects with ``version_number``, ``created_by_id``,
            ``created_at`` and ``name``, in any order.

    Returns:
        The sessions, newest first, each holding its versions newest first.
    """
    sessions: list[EditingSession] = []
    for version in sorted(versions, key=lambda v: v.version_number):
        current = sessions[-1] if sessions else None
        if (
            current is not None
            and current.last.created_by_id == version.created_by_id
            and version.created_at - current.last.created_at <= SESSION_GAP
        ):
            current.versions.insert(0, version)
        else:
            sessions.append(EditingSession(versions=[version]))
    sessions.reverse()
    return sessions
