"""The dataset list's views, filters and order, kept out of the route.

Twenty cards in a grid are useless to search. The table view narrows as the
person types and sorts by any column; the filters are plain query parameters,
so a narrowed table can be bookmarked, and the chosen view is remembered in a
cookie. Everything here is pure, so the rules are tested without a request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

VIEWS = ("cards", "table")
SORTS = ("name", "profile", "version", "entities", "updated", "access")
ACCESS = ("mine", "shared", "collaboration")
VIEW_COOKIE = "dataset_view"


@dataclass(frozen=True)
class DatasetRow:
    """One dataset as the list shows it."""

    dataset: Any
    entities: int
    access: str
    """``mine``, ``shared`` (by a person) or ``collaboration`` (by a grant)."""
    collaboration: str | None
    """The collaboration a grant names, for the badge."""


@dataclass(frozen=True)
class ListFilters:
    """What the person asked the list to show."""

    view: str = "cards"
    q: str = ""
    profile: str = ""
    access: str = ""
    sort: str = "updated"
    direction: str = "desc"

    @classmethod
    def from_query(cls, params: Mapping[str, str], *, remembered: str | None) -> ListFilters:
        """Read the filters off the query string, falling back rather than failing.

        Args:
            params: The request's query parameters.
            remembered: The view the ``dataset_view`` cookie holds, if any.
        """
        view = params.get("view", "")
        if view not in VIEWS:
            view = remembered if remembered in VIEWS else "cards"
        sort = params.get("sort", "updated")
        if sort not in SORTS:
            sort = "updated"
        direction = params.get("dir", "")
        if direction not in ("asc", "desc"):
            direction = "asc" if sort in ("name", "profile", "version", "access") else "desc"
        access = params.get("access", "")
        return cls(
            view=view,
            q=params.get("q", "").strip(),
            profile=params.get("profile", "").strip(),
            access=access if access in ACCESS else "",
            sort=sort,
            direction=direction,
        )

    def narrowed(self) -> bool:
        return bool(self.q or self.profile or self.access)


def _key(row: DatasetRow, sort: str) -> Any:
    if sort == "entities":
        return row.entities
    if sort == "updated":
        return row.dataset.updated_at
    if sort == "access":
        return row.access
    return str(getattr(row.dataset, sort) or "").lower()


def apply_filters(rows: Sequence[DatasetRow], filters: ListFilters) -> list[DatasetRow]:
    """The rows that match, in the order asked for."""
    kept = list(rows)
    if filters.q:
        needle = filters.q.lower()
        kept = [
            r
            for r in kept
            if needle in (r.dataset.name or "").lower()
            or needle in (getattr(r.dataset, "description", "") or "").lower()
            or needle in (r.dataset.profile or "").lower()
        ]
    if filters.profile:
        kept = [r for r in kept if r.dataset.profile == filters.profile]
    if filters.access:
        kept = [r for r in kept if r.access == filters.access]
    kept.sort(key=lambda r: _key(r, filters.sort), reverse=filters.direction == "desc")
    return kept


def profiles_of(rows: Sequence[DatasetRow]) -> list[str]:
    """The profiles the datasets use, for the filter's choices."""
    return sorted({r.dataset.profile for r in rows if r.dataset.profile})
