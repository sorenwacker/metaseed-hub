"""Shared FastAPI dependencies for Hub UI routes."""

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import (
    get_dataset_for_editor as get_dataset_for_editor,
)
from metaseed_hub.access import (
    get_dataset_for_user as get_dataset_for_user,
)
from metaseed_hub.access import (
    get_tenant_for_user as get_tenant_for_user,
)
from metaseed_hub.access import (
    require_dataset_owner as require_dataset_owner,
)
from metaseed_hub.access import (
    tenant_slug_for as tenant_slug_for,
)
from metaseed_hub.access import (
    verify_tenant_access as verify_tenant_access,
)
from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset, Tenant, User
from metaseed_hub.ui.helpers import (
    ensure_dataset_facade,
    normalize_email,
    validate_csrf_token,
)
from metaseed_hub.ui.helpers.load_report import BROWSER_WAY_THROUGH, unloadable_node_refusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from metaseed import SkippedNode

    from metaseed_hub.ui.metaseed_ui import AppState

logger = logging.getLogger("metaseed_hub")

# Single source of truth for the access token cookie name; cookie writers
# (ui.routes.auth, ui.app) import it from here so reads and writes cannot
# silently diverge.
ACCESS_TOKEN_COOKIE = "metaseed_access_token"


class AuthRequiredError(Exception):
    """Raised when authentication is required but user is not authenticated."""

    def __init__(self, is_htmx: bool = False) -> None:
        self.is_htmx = is_htmx
        super().__init__("Authentication required")


class DuplicateAccountEmailError(Exception):
    """Raised when a new OIDC subject presents an email another account holds.

    One address belongs to one account (``uq_users_email``), which is what lets
    sharing resolve an invitee by email. An identity provider that reissues
    subjects -- a rebuilt realm, a re-registered person -- therefore arrives as a
    known address under an unknown subject. Provisioning refuses it: rebinding
    the existing account to the new subject would hand over that account's
    datasets and drafts on the identity provider's say-so, and an admin should
    decide whether the two are the same person.
    """

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"Another account already uses {email}")


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


def handle_duplicate_account_email(request: Request, exc: Exception) -> Response:
    """Report a refused sign-in as a fixable conflict rather than a 500."""
    email = getattr(exc, "email", "that address")
    logger.error(
        "Refused to provision a second account for %s: the address already "
        "belongs to another account. An administrator must merge or remove one.",
        email,
    )
    return Response(
        content=(
            f"Another account already uses {email}. This happens when an identity "
            "provider issues a new subject for an existing person. Ask an "
            "administrator to merge or remove the duplicate account."
        ),
        status_code=409,
        media_type="text/plain",
    )


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
    slug = tenant_slug_for(user.keycloak_id)

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

    # Get or create user. The address is stored lowercased so that sharing, which
    # resolves an invitee by email equality, matches whatever casing the identity
    # provider sent; uq_users_email relies on the same normalization.
    user_result = await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
    db_user = user_result.scalar_one_or_none()
    if not db_user:
        email = normalize_email(user.email)
        # Refuse rather than let uq_users_email raise an IntegrityError on every
        # authenticated page. See DuplicateAccountEmailError for why the existing
        # account is not rebound to this subject.
        taken = await session.execute(select(User).where(User.email == email))
        if taken.scalar_one_or_none() is not None:
            raise DuplicateAccountEmailError(email)
        db_user = User(
            keycloak_id=user.keycloak_id,
            email=email,
            display_name=user.name or user.email.split("@")[0],
            tenant_id=tenant.id,
        )
        session.add(db_user)

    # Commit unconditionally: a newly created tenant must be persisted even when
    # the user already exists, otherwise it is rolled back when the session
    # closes (get_session does not commit on exit).
    await session.commit()

    return tenant, db_user


# Type aliases for cleaner route signatures
CurrentUser = Annotated[TokenUser, Depends(require_user)]
OptionalUser = Annotated[TokenUser | None, Depends(get_current_user_from_cookie)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


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

    # Load the dataset through the editor-scoped helper so mutations enforce
    # tenant/membership scoping, the soft-delete filter, AND the member's role.
    # Every browser table/cell/row edit funnels through here, so this one line
    # is what makes VIEWER mean view (the sharing panel has offered the role
    # since sharing shipped; nothing on the content paths read it).
    dataset = await get_dataset_for_editor(dataset_id, session, user)

    # Get or create AppState for the dataset
    # Use ensure_dataset_facade to properly load specs from database for draft specs.
    # Collect the nodes the load could not place: saving rewrites the dataset from
    # what loaded, so editing one cell is what deletes them. Refusing here covers
    # every browser mutation at once, the way _editing covers every MCP one.
    skipped: list[SkippedNode] = []
    state = await ensure_dataset_facade(dataset, session, on_skip=skipped.append)
    if skipped:
        raise HTTPException(
            status_code=409,
            detail=unloadable_node_refusal(skipped, BROWSER_WAY_THROUGH),
        )

    return dataset, state


def require_feature(feature: str) -> "Callable[..., Awaitable[TokenUser]]":
    """Require that the signed-in user has been granted ``feature``.

    Refuses with 404 rather than 403: a feature someone may not use should not
    advertise that it exists. A 403 tells an unentitled user exactly which
    capabilities are worth asking about, which for a beta flag is the opposite
    of the point.

    Args:
        feature: The feature name, matching a row in ``feature_grants``.

    Returns:
        A dependency yielding the user, so a route can take it in place of
        ``require_user`` rather than in addition to it.
    """

    async def guard(
        user: Annotated[TokenUser, Depends(require_user)],
        session: DbSession,
    ) -> TokenUser:
        from metaseed_hub.features import has_feature

        if not await has_feature(feature, user.entitlements, session):
            raise HTTPException(status_code=404)
        return user

    return guard


async def user_features(request: Request, session: AsyncSession) -> set[str]:
    """Every feature the current visitor may use, for hiding what they cannot reach.

    Returns an empty set for an anonymous visitor rather than raising, because a
    template asks this to decide whether to draw a link, not to authorize.
    """
    from metaseed_hub.features import enabled_features

    user = await get_current_user_from_cookie(request)
    if user is None:
        return set()
    return await enabled_features(user.entitlements, session)
