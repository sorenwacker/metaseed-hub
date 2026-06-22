"""Shared FastAPI dependencies for Hub UI routes."""

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset, DatasetMember, DatasetRole, Tenant, User
from metaseed_hub.ui.helpers import ensure_dataset_facade, validate_csrf_token

if TYPE_CHECKING:
    from metaseed.ui.state import AppState

ACCESS_TOKEN_COOKIE = "metaseed_access_token"


class AuthRequiredError(Exception):
    """Raised when authentication is required but user is not authenticated."""

    def __init__(self, is_htmx: bool = False) -> None:
        self.is_htmx = is_htmx
        super().__init__("Authentication required")


async def get_current_user_from_cookie(request: Request) -> TokenUser | None:
    """Extract and verify user from access token cookie.

    Checks for a refreshed token in request.state first (set by middleware),
    then falls back to the cookie value.

    Returns None if no token or invalid token.
    """
    # Check for refreshed token from middleware
    token = getattr(request.state, "refreshed_access_token", None)
    if not token:
        token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        return await verify_token(token)
    except Exception:
        return None


async def require_user(request: Request) -> TokenUser:
    """Require authenticated user, redirect to login if not authenticated.

    Use as a FastAPI dependency to protect routes.
    Raises AuthRequiredError which is handled by the app exception handler.
    """
    user = await get_current_user_from_cookie(request)
    if not user:
        is_htmx = request.headers.get("HX-Request") == "true"
        raise AuthRequiredError(is_htmx=is_htmx)
    return user


def handle_auth_required_error(request: Request, exc: Exception) -> Response:
    """Handle AuthRequiredError by redirecting to login.

    For HTMX requests, returns 401 with HX-Redirect header.
    For regular requests, returns 302 redirect.
    """
    if isinstance(exc, AuthRequiredError) and exc.is_htmx:
        return Response(
            content="Session expired",
            status_code=401,
            headers={"HX-Redirect": "/hub/auth/login"},
        )
    return RedirectResponse(url="/hub/auth/login", status_code=302)


def unauthorized_response() -> HTMLResponse:
    """Return a user-friendly unauthorized response for HTMX requests."""
    return HTMLResponse(
        """<div class="error-message" style="padding: 1rem;">
            <strong>Session expired.</strong>
            Please <a href="/hub/auth/login" target="_top">log in again</a> to continue.
        </div>""",
        status_code=401,
    )


async def get_dataset_by_id(
    dataset_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dataset:
    """Get dataset by ID or raise 404.

    Use as a FastAPI dependency to load and validate dataset access.
    Note: This function does NOT verify ownership. Use get_dataset_for_user()
    for endpoints that require ownership verification.
    """
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


async def get_tenant_for_user(session: AsyncSession, user: TokenUser) -> Tenant | None:
    """Get tenant for user based on keycloak_id.

    Args:
        session: Database session.
        user: Authenticated user.

    Returns:
        Tenant for the user, or None if not found.
    """
    slug = user.keycloak_id[:8]
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def ensure_tenant_and_user(session: AsyncSession, user: TokenUser) -> tuple[Tenant, User]:
    """Get or create tenant and user for authenticated user.

    Auto-creates tenant and user if they don't exist.
    This simplifies onboarding - users don't need to manually set up.

    Args:
        session: Database session.
        user: Authenticated user.

    Returns:
        Tuple of (Tenant, User).
    """
    slug = user.keycloak_id[:8]

    # Get or create tenant
    tenant_result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(
            name=user.name or user.email.split("@")[0],
            slug=slug,
        )
        session.add(tenant)
        await session.flush()

    # Get or create user
    user_result = await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
    db_user = user_result.scalar_one_or_none()
    if not db_user:
        db_user = User(
            keycloak_id=user.keycloak_id,
            email=user.email,
            display_name=user.name or user.email.split("@")[0],
            tenant_id=tenant.id,
        )
        session.add(db_user)

    # Commit unconditionally: a newly created tenant must be persisted even when
    # the user already exists, otherwise it is rolled back when the session
    # closes (get_session does not commit on exit).
    await session.commit()

    return tenant, db_user


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


# Type aliases for cleaner route signatures
CurrentUser = Annotated[TokenUser, Depends(require_user)]
OptionalUser = Annotated[TokenUser | None, Depends(get_current_user_from_cookie)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


class CSRFValidationError(Exception):
    """Raised when CSRF token validation fails."""

    pass


class DatasetNotFoundError(Exception):
    """Raised when dataset is not found."""

    pass


async def get_dataset_state_for_mutation(
    request: Request,
    dataset_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[Dataset, "AppState"]:
    """Dependency that validates auth, CSRF, and returns dataset with state.

    Use for mutation endpoints (POST, DELETE) that need CSRF validation.

    Args:
        request: The FastAPI request object.
        dataset_id: ID of the dataset to load.
        session: Database session.

    Returns:
        Tuple of (Dataset, AppState) for the validated request.

    Raises:
        HTTPException: 401 if auth fails, 403 if CSRF fails, 404 if dataset not found.
    """
    # Validate authentication
    user = await get_current_user_from_cookie(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # Validate CSRF token
    if not validate_csrf_token(request):
        raise HTTPException(
            status_code=403,
            detail="CSRF validation failed",
        )

    # Load the dataset through the shared access helper so mutations enforce the
    # same tenant/membership scoping and soft-delete filter as reads. Without
    # this, any authenticated user could mutate any dataset by id, and a
    # soft-deleted dataset would remain mutable.
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Get or create AppState for the dataset
    # Use ensure_dataset_facade to properly load specs from database for draft specs
    state = await ensure_dataset_facade(dataset, session)

    return dataset, state
