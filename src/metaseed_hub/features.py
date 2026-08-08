"""Deciding which features a user may use.

Two halves meet here. Group membership comes from the identity provider and is
read from the token (:mod:`metaseed_hub.entitlements`); which feature a group
may use is hub state (:class:`~metaseed_hub.models.FeatureGrant`), because
neither Keycloak nor SRAM models "feature X enabled".

Everything resolves through :func:`enabled_features`, so the route guard and the
template helper can never disagree about what a user may see. The FastAPI
dependency wrapping this lives in :mod:`metaseed_hub.ui.dependencies` -- policy
does not import the web layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from metaseed_hub.entitlements import entitled_urns
from metaseed_hub.models import FeatureGrant

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession


async def enabled_features(entitlements: Iterable[str] | None, session: AsyncSession) -> set[str]:
    """Every feature the holder of ``entitlements`` may use.

    Args:
        entitlements: The raw ``eduperson_entitlement`` values from a verified
            token (``TokenUser.entitlements``), or ``None`` when unauthenticated.
        session: Database session for reading the grants.

    Returns:
        Feature names. Empty when the user is in no group, which is the default
        for everybody: a feature is off until some group is granted it.
    """
    urns = entitled_urns(entitlements)
    if not urns:
        # No group can match a grant, and asking the database would be a query
        # whose answer is already known.
        return set()
    result = await session.execute(
        select(FeatureGrant.feature).where(FeatureGrant.group_urn.in_(urns))
    )
    return set(result.scalars().all())


async def has_feature(
    feature: str, entitlements: Iterable[str] | None, session: AsyncSession
) -> bool:
    """Whether the holder of ``entitlements`` may use ``feature``."""
    return feature in await enabled_features(entitlements, session)
