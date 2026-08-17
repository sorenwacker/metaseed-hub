"""Admin dashboard routes for system monitoring.

Provides GDPR-compliant aggregated statistics and admin-only user management.
Access is controlled via ADMIN_ROLE setting (checks user.roles from OIDC token).
"""

import html
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metaseed_hub.auth import TokenUser
from metaseed_hub.config import get_settings
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset, ErrorEvent, Spec, SpecStatus, User
from metaseed_hub.ui.dependencies import require_user
from metaseed_hub.ui.helpers import validate_csrf_token
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
    """Whether the token grants admin.

    Only membership of the SRAM admin group grants admin -- the same source
    every other feature uses. Realm roles grant nothing: they exist only in the
    dev Keycloak, so an admin path through them would be a door that exists in
    development and not in production.

    ``admin_role`` names the group: either the full URN (exact match), which is
    how production configures it, or a bare name matched as the URN's group
    part (``...:admin`` / ``...#admin``).

    Args:
        user: Authenticated user with entitlements from the token.

    Returns:
        True if the user is in the admin group.
    """
    admin_role = get_admin_role()
    if ":" in admin_role:
        return admin_role in user.entitlements
    return any(
        urn.endswith(f":{admin_role}") or urn.endswith(f"#{admin_role}")
        for urn in user.entitlements
    )


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


async def _spec_counts_by_user(session: AsyncSession) -> dict[str, int]:
    """Return ``{user_id: published spec count}`` for every user who wrote one.

    Counted by ``created_by_id``, the author, rather than by tenant as datasets
    are. The two differ: publishing a draft shared from another account puts
    the specification in *that* account while recording the publisher as its
    author, so counting by tenant would credit it to the wrong person.

    Withdrawn specifications are excluded, so the column matches what is
    actually published.
    """
    result = await session.execute(
        select(User.id, func.count(Spec.id))
        .join(Spec, Spec.created_by_id == User.id)
        .where(
            User.deleted_at.is_(None),
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
        .group_by(User.id)
    )
    return {user_id: count for user_id, count in result.all()}


async def _dataset_counts_by_spec(session: AsyncSession) -> dict[str, int]:
    """Return ``{spec_id: datasets using it}`` for every published spec in use.

    Counted the way the delete refusal counts dependents — by ``spec_id``, with
    soft-deleted datasets excluded — so the page and the refusal cannot
    disagree about what "in use" means. A dataset the owner deleted must not
    keep a specification looking load-bearing forever.

    Deliberately global rather than per tenant: publishing is what makes a
    specification available to other accounts, so a spec published in one and
    used in another is in use. Specs nothing uses are absent from the mapping,
    which the caller reads as zero.

    Args:
        session: Database session.

    Returns:
        Mapping of spec id to the number of datasets built on it.
    """
    result = await session.execute(
        select(Dataset.spec_id, func.count(Dataset.id))
        .where(Dataset.spec_id.is_not(None), Dataset.deleted_at.is_(None))
        .group_by(Dataset.spec_id)
    )
    return {spec_id: count for spec_id, count in result.all()}


async def _dataset_counts_by_draft(session: AsyncSession) -> dict[str, int]:
    """Return ``{draft_id: datasets using it}``, the draft counterpart.

    A draft is the specification its datasets validate against just as much as
    a published spec is, and deleting one is refused for the same reason.

    Args:
        session: Database session.

    Returns:
        Mapping of draft id to the number of datasets built on it.
    """
    result = await session.execute(
        select(Dataset.spec_draft_id, func.count(Dataset.id))
        .where(Dataset.spec_draft_id.is_not(None), Dataset.deleted_at.is_(None))
        .group_by(Dataset.spec_draft_id)
    )
    return {draft_id: count for draft_id, count in result.all()}


async def record_login(session: AsyncSession, user: TokenUser) -> None:
    """Stamp ``last_login_at`` for the user who just completed sign-in.

    Called from the OIDC callback, so it records a sign-in rather than a request,
    and after the account is provisioned so a first sign-in has a row to stamp.
    An unknown subject is still a no-op; and because this is bookkeeping, any
    failure is swallowed — a user must not be locked out because a write to this
    column failed.
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


RECENT_ERROR_LIMIT = 50


async def _recent_errors(
    session: AsyncSession, limit: int = RECENT_ERROR_LIMIT
) -> list[ErrorEvent]:
    """The most recent unhandled errors, newest first, with their caller."""
    result = await session.execute(
        select(ErrorEvent)
        .options(selectinload(ErrorEvent.user))
        .order_by(ErrorEvent.occurred_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _error_counts_by_day(session: AsyncSession, days: int = 7) -> list[tuple[Any, int]]:
    """Errors per day over the recent window, so a spike is visible at a glance."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(func.date(ErrorEvent.occurred_at), func.count(ErrorEvent.id))
        .where(ErrorEvent.occurred_at > cutoff)
        .group_by(func.date(ErrorEvent.occurred_at))
        .order_by(func.date(ErrorEvent.occurred_at).desc())
    )
    return [(day, count) for day, count in result.all()]


# What an admin may act on by identifier. Both are soft-deletable and both are
# things a user can create by mistake; nothing else on the dashboard is.
REMOVABLE: dict[str, type[Dataset] | type[Spec]] = {"dataset": Dataset, "spec": Spec}


class RemovalError(Exception):
    """An admin removal could not be carried out as asked."""


async def _describe_owner(session: AsyncSession, tenant_id: str) -> str:
    """The email of the account that owns an item, for the confirmation.

    A account belongs to one person, so naming them is how an administrator
    sees at a glance that the identifier was the intended one.
    """
    email = await session.scalar(
        select(User.email).where(User.tenant_id == tenant_id, User.deleted_at.is_(None)).limit(1)
    )
    return email or "unknown"


async def set_removed(
    session: AsyncSession,
    kind: str,
    item_id: str,
    *,
    removed: bool,
) -> str:
    """Soft-delete or restore a dataset or spec in any account.

    Removal is by identifier rather than by browsing: the dashboard reports
    aggregated counts and does not list other people's content, and a tool that
    enumerated every dataset in the deployment to find one would undo that.

    Soft rather than hard, because an identifier is easy to mistype and an
    irreversible admin action on someone else's data is not acceptable. The row
    stays, carrying the time it was removed.

    Args:
        session: Database session.
        kind: Either ``"dataset"`` or ``"spec"``.
        item_id: The item's UUID.
        removed: True to remove, False to restore.

    Returns:
        A description of what was acted on, naming the item and its owner, so a
        mistyped identifier is visible immediately.

    Raises:
        RemovalError: If the kind is unknown, or no such item exists.
    """
    model = REMOVABLE.get(kind)
    if model is None:
        raise RemovalError(f"Unknown kind {kind!r}; expected one of {sorted(REMOVABLE)}")

    item = cast("Dataset | Spec | None", await session.get(model, item_id))
    if item is None:
        raise RemovalError(f"No {kind} with id {item_id}")

    already = item.deleted_at is not None
    if removed and already:
        raise RemovalError(f"That {kind} is already removed")
    if not removed and not already:
        raise RemovalError(f"That {kind} is not removed")

    if removed:
        item.soft_delete()
    else:
        item.restore()
    await session.commit()

    owner = await _describe_owner(session, item.tenant_id)
    verb = "Removed" if removed else "Restored"
    return f"{verb} {kind} '{item.name}' owned by {owner}"


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

    # Published specifications, most used first, so the ones nothing uses fall
    # to the bottom where they are easy to spot.
    spec_usage = await _dataset_counts_by_spec(session)
    specs_result = await session.execute(
        select(Spec)
        .options(selectinload(Spec.created_by))
        .where(Spec.deleted_at.is_(None), Spec.status == SpecStatus.PUBLISHED)
    )
    specs_in_use = sorted(
        specs_result.scalars().all(),
        key=lambda spec: (-spec_usage.get(spec.id, 0), spec.name.lower(), spec.version),
    )

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
            "spec_counts": await _spec_counts_by_user(session),
            "specs_in_use": specs_in_use,
            "spec_usage": spec_usage,
            "recent_errors": await _recent_errors(session),
            "error_counts": await _error_counts_by_day(session),
            "using_default_secret_key": get_settings().using_default_secret_key,
            "nav_active": "admin",
        },
    )


@router.post("/content/{action}", response_class=HTMLResponse)
async def admin_change_content(
    request: Request,
    action: str,
    session: DbSession,
    user: AdminUser,
    kind: Annotated[str, Form()],
    item_id: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> HTMLResponse:
    """Remove or restore a dataset or specification in any account.

    Args:
        request: The request, for CSRF validation.
        action: Either ``remove`` or ``restore``.
        session: Database session.
        user: The administrator, enforced by the dependency.
        kind: ``dataset`` or ``spec``.
        item_id: The item's UUID.
        csrf_token: The double-submit token from the form.
    """
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if action not in ("remove", "restore"):
        raise HTTPException(status_code=404, detail="Unknown action")

    try:
        message = await set_removed(
            session, kind.strip(), item_id.strip(), removed=action == "remove"
        )
    except RemovalError as exc:
        return HTMLResponse(
            f"<div class='notification error'>{html.escape(str(exc))}</div>",
            status_code=400,
        )

    # Logged because this is one person acting on another's data: the dashboard
    # shows aggregates, so without this there would be no record of who did it.
    logger.warning("admin %s: %s", user.email, message)
    return HTMLResponse(f"<div class='notification success'>{html.escape(message)}</div>")
