"""Looking up a user's SEEK connection.

Its own module because two routers need it — the SEEK routes and the dataset
page, which shows the connection's standing next to the push button — and a
route importing another route to find it would be a hidden dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from metaseed_hub.models import SeekConnection, Tenant
from metaseed_hub.ui.dependencies import tenant_slug_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.auth import TokenUser


async def tenant_for_user(session: AsyncSession, user: TokenUser) -> Tenant | None:
    """The tenant owning ``user``'s datasets, or ``None`` before first use."""
    result = await session.execute(
        select(Tenant).where(Tenant.slug == tenant_slug_for(user.keycloak_id))
    )
    tenant: Tenant | None = result.scalar_one_or_none()
    return tenant


async def connection_for_user(session: AsyncSession, user: TokenUser) -> SeekConnection | None:
    """The user's stored SEEK connection, working or not."""
    tenant = await tenant_for_user(session, user)
    if tenant is None:
        return None
    result = await session.execute(
        select(SeekConnection).where(SeekConnection.tenant_id == tenant.id)
    )
    connection: SeekConnection | None = result.scalar_one_or_none()
    return connection
