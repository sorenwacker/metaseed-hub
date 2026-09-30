"""Grouping the drafts list by specification rather than by row.

A draft is one name at one version, so holding several versions of a profile
is ordinary rather than a mistake. Listed one card per row, that is several
cards carrying the same title with only a version number to tell them apart.
This turns the rows into one group per specification, newest version first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from metaseed.specs.versioning import version_sort_key

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
        ordered = sorted(rows, key=lambda d: version_sort_key(d.version), reverse=True)
        groups.append(DraftGroup(name=name, label=spec_label(ordered[0]), drafts=ordered))
    return sorted(groups, key=lambda g: g.label.lower())
