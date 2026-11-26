"""Which repository integrations a person has switched on.

A feature is a repository integration metaseed ships as an adapter: importing
from or exporting to ENA, PRIDE, BrAPI, MetaboLights, pushing to SEEK, the
DCAT export. Every signed-in user may use every one; each is off until the
user switches it on on their profile page. SRAM groups play no part.

The set is read once per request by
:func:`metaseed_hub.ui.dependencies.remember_features` and kept on
``request.state``, so the page's header, the page itself and the route it
posts to agree.

See docs/administration.md, *Repository integrations*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from metaseed_hub.models import UserFeature

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: The integrations a user can switch on, one per adapter metaseed ships.
FEATURES = frozenset({"ena", "pride", "brapi", "metabolights", "seek", "dcat"})


async def features_of(session: AsyncSession, user_id: str) -> set[str]:
    """The integrations ``user_id`` has switched on."""
    rows = await session.scalars(select(UserFeature.feature).where(UserFeature.user_id == user_id))
    return set(rows) & FEATURES


async def set_feature(session: AsyncSession, user_id: str, feature: str, *, enabled: bool) -> None:
    """Switch ``feature`` on or off for ``user_id``; the caller commits.

    Raises:
        ValueError: ``feature`` is not one of :data:`FEATURES`.
    """
    if feature not in FEATURES:
        raise ValueError(f"unknown integration: {feature!r}")
    if enabled:
        await session.execute(
            insert(UserFeature).values(user_id=user_id, feature=feature).on_conflict_do_nothing()
        )
    else:
        await session.execute(
            delete(UserFeature).where(
                UserFeature.user_id == user_id, UserFeature.feature == feature
            )
        )


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
