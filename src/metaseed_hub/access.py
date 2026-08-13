"""Who may reach which dataset. One answer for every layer.

The UI, the REST API, the MCP endpoint and the websocket all have to agree on
tenancy and sharing, and they did not: the REST API re-implemented a
tenant-only check and ignored ``DatasetMember`` rows the UI honoured, and it
imported its tenancy helpers from the UI layer to do so. Access control is not
a UI concern, so it lives here and the layers import it.

The ladder, least to most privileged:

- :func:`get_dataset_for_user` — any member (VIEWER included) or the owning
  tenant. Reads and comments.
- :func:`get_dataset_for_editor` — the owning tenant, or a member whose role
  is in :data:`~metaseed_hub.sharing.EDIT_ROLES`. Content mutations.
- :func:`require_dataset_owner` — the owning tenant or an OWNER member.
  Membership changes, and destroying the dataset itself.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, DatasetMember, DatasetRole, Tenant, User
from metaseed_hub.sharing import EDIT_ROLES

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


async def get_dataset_for_user(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset if user has access through tenant or sharing.

    A user has access to a dataset if:
    1. Their tenant owns the dataset, OR
    2. They have been granted access via DatasetMember

    Args:
        dataset_id: ID of the dataset to retrieve.
        session: Database session.
        user: Authenticated user.

    Returns:
        Dataset if user has access.

    Raises:
        HTTPException: 404 if dataset not found, 403 if access denied.
    """
    result = await session.execute(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.deleted_at.is_(None))
    )
    dataset = result.scalar_one_or_none()

    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # First, try tenant access (owner)
    try:
        await verify_tenant_access(dataset.tenant_id, session, user)
        return dataset
    except HTTPException:
        pass  # Not tenant owner, check DatasetMember

    # Check if user has access via DatasetMember
    db_user_result = await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
    db_user = db_user_result.scalar_one_or_none()

    if db_user:
        member_result = await session.execute(
            select(DatasetMember).where(
                DatasetMember.dataset_id == dataset_id,
                DatasetMember.user_id == db_user.id,
            )
        )
        if member_result.scalar_one_or_none():
            return dataset

    raise HTTPException(status_code=403, detail="Access denied")


async def get_dataset_for_editor(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset only if the user may change its content.

    The sharing panel offers VIEWER for exactly one reason: to let someone look
    without letting them change anything. Content mutations therefore authorize
    through this helper — the tenant owner, or a member whose role is in
    :data:`~metaseed_hub.sharing.EDIT_ROLES` — while reads and comments stay on
    :func:`get_dataset_for_user`, which any member passes.

    Args:
        dataset_id: ID of the dataset to retrieve.
        session: Database session.
        user: Authenticated user.

    Returns:
        Dataset if the user may edit it.

    Raises:
        HTTPException: 404 if dataset not found, 403 if the user has no access
            or only view access.
    """
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Tenant owners may always edit their own data.
    try:
        await verify_tenant_access(dataset.tenant_id, session, user)
        return dataset
    except HTTPException:
        pass  # Not tenant owner; the membership's role decides.

    db_user_result = await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
    db_user = db_user_result.scalar_one_or_none()
    if db_user:
        member_result = await session.execute(
            select(DatasetMember).where(
                DatasetMember.dataset_id == dataset_id,
                DatasetMember.user_id == db_user.id,
                DatasetMember.role.in_(EDIT_ROLES),
            )
        )
        if member_result.scalar_one_or_none():
            return dataset

    raise HTTPException(
        status_code=403,
        detail="This dataset is shared with you to view, not to change.",
    )


async def require_dataset_owner(
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Get dataset only if the user owns it (tenant owner or OWNER member).

    Membership-management mutations must be restricted to owners. An ordinary
    member (including a VIEWER) can read a dataset via get_dataset_for_user but
    must not add, remove, or re-role members.

    Args:
        dataset_id: ID of the dataset to retrieve.
        session: Database session.
        user: Authenticated user.

    Returns:
        Dataset if the user is an owner.

    Raises:
        HTTPException: 404 if dataset not found, 403 if the user is not an owner.
    """
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Tenant owners are always dataset owners.
    try:
        await verify_tenant_access(dataset.tenant_id, session, user)
        return dataset
    except HTTPException:
        pass  # Not tenant owner, require an OWNER-role membership.

    db_user_result = await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
    db_user = db_user_result.scalar_one_or_none()
    if db_user:
        owner_result = await session.execute(
            select(DatasetMember).where(
                DatasetMember.dataset_id == dataset_id,
                DatasetMember.user_id == db_user.id,
                DatasetMember.role == DatasetRole.OWNER,
            )
        )
        if owner_result.scalar_one_or_none():
            return dataset

    raise HTTPException(status_code=403, detail="Owner access required")
