"""Who may reach which dataset. One answer for every layer.

The UI, the REST API, the MCP endpoint and the websocket all have to agree on
tenancy and sharing, and they did not: the REST API re-implemented a
tenant-only check and ignored ``DatasetMember`` rows the UI honoured, and it
imported its tenancy helpers from the UI layer to do so. Access control is not
a UI concern, so it lives here and the layers import it.

The ladder, least to most privileged, each reading the one role that
:func:`metaseed_hub.sharing.role_of` gives the caller:

- :func:`get_dataset_for_user` — any role. Reads and comments.
- :func:`get_dataset_for_editor` — a role in
  :data:`~metaseed_hub.sharing.EDIT_ROLES`. Content mutations.
- :func:`require_dataset_owner` — OWNER. Membership changes, and destroying
  the dataset itself.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, Tenant, User
from metaseed_hub.sharing import EDIT_ROLES, Role, resource_for, role_of

logger = logging.getLogger("metaseed_hub")


def tenant_slug_for(keycloak_id: str) -> str:
    """Return the tenant slug for an OIDC subject.

    The slug is a 32-hex-character (128-bit) SHA-256 prefix of the full subject.
    It must derive from the *entire* ``keycloak_id``: a shorter truncation of the
    subject makes the tenant boundary collision-prone, and a collision would map
    two distinct users into one tenant with full access to each other's data.

    Args:
        keycloak_id: The OIDC subject (``sub``) of the authenticated user.

    Returns:
        A deterministic 32-character hex slug.
    """
    return hashlib.sha256(keycloak_id.encode()).hexdigest()[:32]


async def get_tenant_for_user(session: AsyncSession, user: TokenUser) -> Tenant | None:
    """Get tenant for user based on keycloak_id.

    Args:
        session: Database session.
        user: Authenticated user.

    Returns:
        Tenant for the user, or None if not found.
    """
    slug = tenant_slug_for(user.keycloak_id)
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def verify_tenant_access(
    tenant_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Tenant:
    """Verify user has access to tenant and return it.

    A user has access to a tenant if their keycloak_id matches the tenant slug.

    Args:
        tenant_id: ID of the tenant to verify.
        session: Database session.
        user: Authenticated user.

    Returns:
        Tenant if user has access.

    Raises:
        HTTPException: 404 if tenant not found, 403 if access denied.
    """
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Get user's tenant
    user_tenant = await get_tenant_for_user(session, user)
    if not user_tenant:
        raise HTTPException(status_code=403, detail="Access denied")

    # Verify tenant matches user's tenant
    if tenant.id != user_tenant.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return tenant


async def live_user(session: AsyncSession, user: TokenUser) -> User | None:
    """Resolve the caller's account, treating a soft-deleted one as absent.

    Admin deletion sets ``deleted_at`` and deliberately leaves DatasetMember
    rows in place, so resolving by ``keycloak_id`` alone let a deleted user
    keep membership-based access for as long as their token stayed valid. The
    MCP layer already enforces this; the three access-ladder helpers share this
    lookup so they cannot drift apart from it again.

    Args:
        session: Database session.
        user: The authenticated caller.

    Returns:
        The live User row, or None when there is none.
    """
    found = await session.execute(
        select(User).where(
            User.keycloak_id == user.keycloak_id,
            User.deleted_at.is_(None),
        )
    )
    return found.scalar_one_or_none()


async def _dataset_and_role(
    dataset_id: str, session: AsyncSession, user: TokenUser
) -> tuple[Dataset, Role | None]:
    """The dataset and the caller's role in it, or 404 if there is no dataset."""
    result = await session.execute(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.deleted_at.is_(None))
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db_user = await live_user(session, user)
    if db_user is None:
        return dataset, None
    return dataset, await role_of(session, resource_for("dataset"), dataset.id, db_user.id)


async def get_dataset_for_user(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset if the user has any role in it: reads and comments.

    :func:`metaseed_hub.sharing.role_of` decides — a membership, or the
    account the dataset lives in.

    Raises:
        HTTPException: 404 if dataset not found, 403 if access denied.
    """
    dataset, role = await _dataset_and_role(dataset_id, session, user)
    if role is None:
        raise HTTPException(status_code=403, detail="Access denied")
    return dataset


async def get_dataset_for_editor(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset only if the user may change its content.

    The sharing panel offers VIEWER for exactly one reason: to let someone look
    without letting them change anything. Content mutations authorize here —
    a role in :data:`~metaseed_hub.sharing.EDIT_ROLES` — while reads and
    comments stay on :func:`get_dataset_for_user`.

    Raises:
        HTTPException: 404 if dataset not found, 403 if the user has no access
            or only view access.
    """
    dataset, role = await _dataset_and_role(dataset_id, session, user)
    if role is None:
        raise HTTPException(status_code=403, detail="Access denied")
    if role not in EDIT_ROLES:
        raise HTTPException(
            status_code=403,
            detail="This dataset is shared with you to view, not to change.",
        )
    return dataset


async def require_dataset_owner(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset only if the user owns it.

    Membership management and destroying the dataset are owner-only. An
    ordinary member (including a VIEWER) can read a dataset but must not add,
    remove, or re-role members.

    Raises:
        HTTPException: 404 if dataset not found, 403 if the user is not an owner.
    """
    dataset, role = await _dataset_and_role(dataset_id, session, user)
    if role is None:
        raise HTTPException(status_code=403, detail="Access denied")
    if role is not Role.OWNER:
        raise HTTPException(status_code=403, detail="Owner access required")
    return dataset
