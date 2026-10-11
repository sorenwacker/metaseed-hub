"""Grouping the specification lists by specification rather than by row.

A draft is one name at one version, and a published specification is one
release of a name, so holding several versions of a profile is ordinary rather
than a mistake. Listed one card per row, that is several cards carrying the
same title with only a version number to tell them apart. This turns the rows
into one group per specification, newest version first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from metaseed.specs.versioning import version_sort_key

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Sequence


@dataclass(frozen=True)
class SpecificationGroup:
    """Every version of one specification, newest first.

    Attributes:
        name: The specification's name, which is what makes the group.
        label: What to show as the group's title, from the newest version.
        versions: The rows, newest version first.
        one_audience: Whether every published version went to the same
            audience, so the card may name it once. False for drafts, which
            have none, and when the versions' audiences differ.
        audience_urn: That audience when ``one_audience``; None is everyone.
    """

    name: str
    label: str
    versions: Sequence[Any]
    one_audience: bool = False
    audience_urn: str | None = None


def group_by_specification(
    rows: Iterable[Any], *, key: Callable[[Any], Hashable] | None = None
) -> list[SpecificationGroup]:
    """The rows as one group per specification, groups sorted by label.

    Args:
        rows: Draft or published specification rows, in any order.
        key: What makes two rows the same specification; the name by default.

    Returns:
        One :class:`SpecificationGroup` per distinct key. Within a group the
        rows are newest version first; the label comes from that newest row,
        so a display name added in the latest version is the one shown.
    """
    from metaseed_hub.ui.spec_builder_helpers import spec_label

    same = key or (lambda row: row.name)
    by_key: dict[Hashable, list[Any]] = {}
    for row in rows:
        by_key.setdefault(same(row), []).append(row)

    groups = []
    for same_rows in by_key.values():
        ordered = sorted(same_rows, key=lambda r: version_sort_key(r.version), reverse=True)
        groups.append(
            SpecificationGroup(name=ordered[0].name, label=spec_label(ordered[0]), versions=ordered)
        )
    return sorted(_told_apart(groups), key=lambda g: g.label.lower())


def _told_apart(groups: list[SpecificationGroup]) -> list[SpecificationGroup]:
    """Name the specification in a label that another group also carries.

    A display name is copied from the template a draft was cloned from, so two
    drafts cloned from one template have the same title and nothing else on
    the card to tell them apart.
    """
    shared = {
        label for label in (g.label for g in groups) if sum(g.label == label for g in groups) > 1
    }
    return [
        SpecificationGroup(
            name=g.name,
            label=f"{g.label} ({g.name})" if g.label in shared and g.label != g.name else g.label,
            versions=g.versions,
            one_audience=g.one_audience,
            audience_urn=g.audience_urn,
        )
        for g in groups
    ]


def group_published(specs: Iterable[Any]) -> list[SpecificationGroup]:
    """Published specifications grouped within the account each lives in.

    Two accounts may each release a specification of the same name; those are
    two cards. The group carries the one audience all its versions were
    published to, so the card can say it once; versions released to different
    audiences leave it unset and each row says its own.

    Args:
        specs: Published ``Spec`` rows, in any order.

    Returns:
        One group per account and name, as :func:`group_by_specification`.
    """
    groups = group_by_specification(specs, key=lambda spec: (spec.tenant_id, spec.name))
    published = []
    for group in groups:
        audiences = {spec.audience_urn for spec in group.versions}
        published.append(
            SpecificationGroup(
                name=group.name,
                label=group.label,
                versions=group.versions,
                one_audience=len(audiences) == 1,
                audience_urn=audiences.pop() if len(audiences) == 1 else None,
            )
        )
    return published
