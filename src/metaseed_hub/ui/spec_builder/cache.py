"""State cache for spec builder drafts.

Provides a bounded LRU cache for draft states to reduce database queries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import SpecBuilderState

logger = logging.getLogger(__name__)

_STATE_CACHE_MAX_SIZE = 100


class StateCache:
    """Bounded LRU cache for draft states.

    Uses an OrderedDict-like pattern to maintain LRU ordering and enforce max size.
    Thread-safety note: Sufficient for single-process deployments.
    For multi-process deployments, consider using Redis or similar.
    """

    def __init__(self, max_size: int = _STATE_CACHE_MAX_SIZE) -> None:
        self._cache: dict[str, SpecBuilderState] = {}
        self._max_size = max_size

    def get(self, key: str) -> SpecBuilderState | None:
        """Get item from cache, moving it to end (most recently used)."""
        if key in self._cache:
            value = self._cache.pop(key)
            self._cache[key] = value
            return value
        return None

    def set(self, key: str, value: SpecBuilderState) -> None:
        """Set item in cache, evicting oldest if at capacity."""
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            logger.debug("Evicted draft %s from state cache", oldest_key)
        self._cache[key] = value

    def pop(self, key: str, default: SpecBuilderState | None = None) -> SpecBuilderState | None:
        """Remove and return item from cache."""
        return self._cache.pop(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._cache


# Global cache instance
state_cache = StateCache()
