"""Grouping the drafts list by specification rather than by row.

A draft is one name at one version, so holding several versions of a profile
is ordinary rather than a mistake. Listed one card per row, that is several
cards carrying the same title with only a version number to tell them apart.
This turns the rows into one group per specification, newest version first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True)
class DraftGroup:
    """Every draft of one specification, newest version first.

    Attributes:
        name: The specification's name, which is what makes the group.
        label: What to show as the group's title, from the newest draft.
        drafts: The drafts, newest version first.
    """

    name: str
    label: str
    drafts: Sequence[Any]


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for a ``MAJOR.MINOR`` version, ordering numerically.

    A string sort puts ``1.10`` before ``1.3``, which reads as the newest
    version being the oldest. A version that is not numeric sorts before every
    numeric one, so it lands last under a descending sort rather than raising:
    the drafts table accepts any string, and a list must not refuse to render
    what the table accepts.
    """
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        return (-1,)
    return tuple(int(part) for part in parts)


def group_by_specification(drafts: Iterable[Any]) -> list[DraftGroup]:
    """The drafts as one group per specification name, groups sorted by label.

    Args:
        drafts: Draft rows, in any order.

    Returns:
        One :class:`DraftGroup` per distinct name. Within a group the drafts
        are newest version first; the label comes from that newest draft, so a
        display name added in the latest version is the one shown.
    """
    from metaseed_hub.ui.spec_builder_helpers import spec_label

    by_name: dict[str, list[Any]] = {}
    for draft in drafts:
        by_name.setdefault(draft.name, []).append(draft)

    groups = []
    for name, rows in by_name.items():
        ordered = sorted(rows, key=lambda d: version_key(d.version), reverse=True)
        groups.append(DraftGroup(name=name, label=spec_label(ordered[0]), drafts=ordered))
    return sorted(groups, key=lambda g: g.label.lower())
