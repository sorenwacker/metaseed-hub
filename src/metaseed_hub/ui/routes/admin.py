"""Admin dashboard routes for system monitoring.

Provides GDPR-compliant aggregated statistics and admin-only user management.
Access is controlled via ADMIN_ROLE setting (checks user.roles from OIDC token).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.config import get_settings
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset, User
from metaseed_hub.ui.dependencies import require_user
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/admin", tags=["admin"])


def init_templates(templates: "Jinja2Templates") -> None:  # noqa: F821
    """Initialize templates reference."""
    _init_render_templates(templates)


def get_admin_role() -> str:
    """Get the admin role name from settings.

    Returns:
        Role name that grants admin access (default: "admin").
    """
    return get_settings().admin_role


def _has_admin_role(user: TokenUser) -> bool:
    """Check if user has the admin role in their token.

    Checks user.roles which is populated from:
    - SRAM: eduperson_entitlement claim
    - Keycloak: realm_access.roles claim

    Args:
        user: Authenticated user with roles from OIDC token.

    Returns:
        True if user has admin role, False otherwise.
    """
    admin_role = get_admin_role()
    # Check for exact match or suffix match (for URN-style entitlements)
    for role in user.roles:
        if role == admin_role or role.endswith(f":{admin_role}") or role.endswith(f"#{admin_role}"):
            return True
    return False


async def require_admin(
    user: Annotated[TokenUser, Depends(require_user)],
) -> TokenUser:
    """Dependency that requires admin access.

    Args:
        user: Authenticated user from require_user dependency.

    Returns:
        The authenticated user if they have admin access.

    Raises:
        HTTPException: 403 if user does not have admin role.
    """
    if not _has_admin_role(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


AdminUser = Annotated[TokenUser, Depends(require_admin)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


def is_admin(user: TokenUser | None) -> bool:
    """Check if user has admin access.

    Args:
        user: User to check, or None if not authenticated.

    Returns:
        True if user has admin role, False otherwise.
    """
    if not user:
        return False
    return _has_admin_role(user)


async def _dataset_counts_by_user(session: AsyncSession) -> dict[str, int]:
    """Return ``{user_id: dataset count}`` for every user who owns datasets.

    A dataset belongs to a tenant, and the hub gives each user a tenant of their
    own on first sign-in, so a user's datasets are their tenant's datasets. Users
    who own none are absent; callers render 0 for a missing key rather than the
    query inventing a row.
    """
    result = await session.execute(
        select(User.id, func.count(Dataset.id))
        .join(Dataset, Dataset.tenant_id == User.tenant_id)
        .where(User.deleted_at.is_(None), Dataset.deleted_at.is_(None))
        .group_by(User.id)
    )
    return {user_id: count for user_id, count in result.all()}


async def record_login(session: AsyncSession, user: TokenUser) -> None:
    """Stamp ``last_login_at`` for the user who just completed sign-in.

    Called from the OIDC callback, so it records a sign-in rather than a request.
    The user row is created lazily on the first page, so an unknown subject is a
    no-op; and because this is bookkeeping, any failure is swallowed — a user
    must not be locked out because a write to this column failed.
    """
    try:
        db_user = (
            await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
        ).scalar_one_or_none()
        if db_user is None:
            return
        db_user.last_login_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        logger.exception("Could not record the sign-in for %s", user.keycloak_id)


@router.get("/")
async def admin_dashboard(
    request: Request,
    session: DbSession,
    user: AdminUser,
) -> Response:
    """Admin dashboard with system statistics.

    Shows aggregated metrics (GDPR-compliant):
    - Total counts of users and datasets
    - User registration activity over last 30 days
    - Dataset creation activity over last 30 days
    - User directory for admin contact purposes
    """
    # Aggregated counts
    stats = {
        "users": await session.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None)))
        or 0,
        "datasets": await session.scalar(
            select(func.count(Dataset.id)).where(Dataset.deleted_at.is_(None))
        )
        or 0,
    }

    # Activity over time (last 30 days) - aggregated counts only
    cutoff = datetime.now(UTC) - timedelta(days=30)

    # User registrations per day
    user_activity_result = await session.execute(
        select(func.date(User.created_at).label("date"), func.count(User.id).label("count"))
        .where(User.created_at > cutoff, User.deleted_at.is_(None))
        .group_by(func.date(User.created_at))
        .order_by(func.date(User.created_at))
    )
    user_activity = [(row.date, row.count) for row in user_activity_result]

    # Dataset creations per day
    dataset_activity_result = await session.execute(
        select(func.date(Dataset.created_at).label("date"), func.count(Dataset.id).label("count"))
        .where(Dataset.created_at > cutoff, Dataset.deleted_at.is_(None))
        .group_by(func.date(Dataset.created_at))
        .order_by(func.date(Dataset.created_at))
    )
    dataset_activity = [(row.date, row.count) for row in dataset_activity_result]

    # User list for admin
    users_result = await session.execute(
        select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())
    )
    users = list(users_result.scalars().all())

    return render_template(
        request=request,
        name="admin/dashboard.html",
        context={
            "user": user,
            "stats": stats,
            "user_activity": user_activity,
            "dataset_activity": dataset_activity,
            "users": users,
            "dataset_counts": await _dataset_counts_by_user(session),
            "using_default_secret_key": get_settings().using_default_secret_key,
            "nav_active": "admin",
        },
    )
