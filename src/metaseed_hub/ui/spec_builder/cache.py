"""State cache for spec builder drafts.

Provides a bounded LRU cache for draft states to reduce database queries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .state import SpecBuilderState

logger = logging.getLogger(__name__)

_STATE_CACHE_MAX_SIZE = 100


class StateCache:
    """Bounded LRU cache for draft states, tagged with the row revision.

    Each entry records the draft row's ``updated_at`` at the time it was read.
    Callers compare that against the stored row and rebuild when it has moved,
    which is what makes the cache safe across processes: every worker has its
    own cache, so an entry can be made stale by a write this process never saw.
    Uses an OrderedDict-like pattern to maintain LRU ordering and enforce max
    size.
    """

    def __init__(self, max_size: int = _STATE_CACHE_MAX_SIZE) -> None:
        self._cache: dict[str, tuple[SpecBuilderState, datetime | None]] = {}
        self._max_size = max_size

    def get(self, key: str) -> SpecBuilderState | None:
        """Get item from cache, moving it to end (most recently used)."""
        if key in self._cache:
            entry = self._cache.pop(key)
            self._cache[key] = entry
            return entry[0]
        return None

    def revision(self, key: str) -> datetime | None:
        """Return the row revision this entry was read at, or None if absent."""
        entry = self._cache.get(key)
        return entry[1] if entry else None

    def set(self, key: str, value: SpecBuilderState, revision: datetime | None = None) -> None:
        """Set item in cache, evicting oldest if at capacity."""
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            logger.debug("Evicted draft %s from state cache", oldest_key)
        self._cache[key] = (value, revision)

    def pop(self, key: str, default: SpecBuilderState | None = None) -> SpecBuilderState | None:
        """Remove and return item from cache."""
        entry = self._cache.pop(key, None)
        return entry[0] if entry else default

    def __contains__(self, key: str) -> bool:
        return key in self._cache


# Global cache instance
state_cache = StateCache()
