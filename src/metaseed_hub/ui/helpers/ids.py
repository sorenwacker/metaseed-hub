"""Path-segment identifiers, checked before they reach a UUID column."""

from __future__ import annotations

import uuid


def is_uuid(value: str) -> bool:
    """Whether ``value`` parses as a UUID.

    Comparing a UUID column with an arbitrary path segment is a DBAPIError on
    Postgres, a 500 where a route means 404. Routes check with this first.
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
