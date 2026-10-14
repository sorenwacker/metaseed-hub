"""Which repository features a person may use, from their SRAM groups.

A feature is a repository integration metaseed ships as an adapter: importing
from or exporting to ENA, PRIDE, BrAPI, MetaboLights, pushing to SEEK, the
DCAT export. SRAM grants one through a group named after it, in any
collaboration the person is in: the application offers the group names, and
each collaboration's admins decide who gets them. This is deliberately the
opposite of the admin rule (:mod:`metaseed_hub.ui.routes.admin`), which is
the exact URN of one group.

The membership read is the snapshot recorded at sign-in
(:mod:`metaseed_hub.collaborations`), because a session carries no
entitlements. It is taken once per request by
:func:`metaseed_hub.ui.dependencies.remember_features` and kept on
``request.state``, so the page's header, the page itself and the route it
posts to agree.

See docs/administration.md, *Features and SRAM groups*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from metaseed_hub.collaborations import entitled_urns_of
from metaseed_hub.entitlements import groups

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

#: The group names SRAM offers, one per adapter metaseed ships.
FEATURES = frozenset({"ena", "pride", "brapi", "metabolights", "seek", "dcat"})


def feature_named(group_name: str | None) -> str | None:
    """The feature a group name grants: the name itself, or what follows its last hyphen.

    SRAM reports ``metaseed-seek`` for a group named ``seek`` once a second
    collaboration is connected to the application, so the prefix is SRAM's,
    not the collaboration's choice. Only a whole hyphen-separated suffix counts:
    ``seeker`` and ``seek-old`` grant nothing.
    """
    if group_name is None:
        return None
    name = group_name.rsplit("-", 1)[-1]
    return name if name in FEATURES else None


def features_in(urns: Iterable[str]) -> set[str]:
    """The features the group URNs grant: a group named after one, anywhere."""
    return {feature for group in groups(urns) if (feature := feature_named(group.group))}


async def features_of(session: AsyncSession, user_id: str) -> set[str]:
    """The features ``user_id`` may use, from their recorded membership."""
    return features_in(await entitled_urns_of(session, user_id))


def feature_of_action(action_key: str) -> str | None:
    """The feature an adapter action belongs to: its adapter's key, if a feature."""
    from metaseed import adapters

    for adapter in adapters.all_adapters():
        if any(action.key == action_key for action in adapter.actions):
            return adapter.key if adapter.key in FEATURES else None
    return None


def granted(request: Request) -> set[str]:
    """The features of the person making ``request``, as remembered for it.

    Nothing unless :func:`metaseed_hub.ui.dependencies.remember_features`
    ran and left a set: a request built by hand grants nothing.
    """
    features = getattr(getattr(request, "state", None), "features", None)
    return set(features) if isinstance(features, (set, frozenset)) else set()
