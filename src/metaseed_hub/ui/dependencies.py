"""Shared FastAPI dependencies for Hub UI routes."""

import logging
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlencode, urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
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
    ensure_dataset_facade_for_write,
    normalize_email,
    validate_csrf_token,
)

if TYPE_CHECKING:
    from metaseed_hub.ui.metaseed_ui import AppState

logger = logging.getLogger("metaseed_hub")

# Single source of truth for the access token cookie name; cookie writers
# (ui.routes.auth, ui.app) import it from here so reads and writes cannot
# silently diverge.
ACCESS_TOKEN_COOKIE = "metaseed_access_token"


LOGIN_PATH = "/hub/auth/login"
"""Where a request without a usable session is sent."""


class AuthRequiredError(Exception):
    """Raised when a request needs a session and does not have a usable one.

    The single way the application reports a missing or expired session. The
    handler below decides the shape -- redirect, ``HX-Redirect``, or JSON --
    from the request, so no route has to remember which of the three its caller
    can act on. A route that writes its own 401 instead writes one without the
    redirect header, and htmx then logs it to the console and leaves the page
    looking as though it worked; ``tests/test_session_expiry.py`` gates on it.

    Args:
        as_json: For endpoints read by ``fetch`` rather than by htmx or the
            browser's address bar. A redirect would be followed to the identity
            provider and fail opaquely, so those get a 401 naming the sign-in
            page in the body.
    """

    def __init__(self, *, as_json: bool = False) -> None:
        self.as_json = as_json
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
        raise AuthRequiredError()
    return user


def _requested_path(request: Request) -> str:
    """The page to return the user to once they have signed in.

    For an htmx request that is the page the user is looking at, not the panel
    or the cell that happened to make the call -- ``HX-Current-URL`` carries
    it. Everything else is the request's own path, which is the full one
    including the ``/hub`` mount.
    """
    current = request.headers.get("HX-Current-URL")
    if current:
        parsed = urlparse(current)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def login_url_for(request: Request) -> str:
    """The sign-in URL that returns to where this request was headed.

    ``next`` is dropped unless it is a path on this hub: it reaches the
    callback from a cookie the browser holds, and an absolute or
    protocol-relative value would make the sign-in page an open redirect.
    """
    target = _requested_path(request)
    if not target.startswith("/hub") or target.startswith("//"):
        return LOGIN_PATH
    return f"{LOGIN_PATH}?{urlencode({'next': target})}"


def handle_auth_required_error(request: Request, exc: Exception) -> Response:
    """Send a request without a usable session to the sign-in page.

    Three shapes, one decision, so a caller never has to interpret a refusal it
    cannot act on: htmx follows ``HX-Redirect`` (which it reads before it looks
    at the status), ``fetch`` reads ``login_url`` out of the body, and a plain
    navigation is redirected.
    """
    login_url = login_url_for(request)

    if request.headers.get("HX-Request") == "true":
        return Response(
            content="Session expired",
            status_code=401,
            headers={"HX-Redirect": login_url},
        )

    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept and "text/html" not in accept
    if wants_json or (isinstance(exc, AuthRequiredError) and exc.as_json):
        return JSONResponse(
            {"error": "Authentication required", "login_url": login_url},
            status_code=401,
        )

    return RedirectResponse(url=login_url, status_code=302)


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
    # Validate authentication. Every browser edit arrives here, so an expired
    # session has to end on the sign-in page rather than in the console: this
    # refused with a bare 401 and htmx left the page looking as though the edit
    # had been saved.
    user = await get_current_user_from_cookie(request)
    if not user:
        raise AuthRequiredError()

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

    # The write-path loader: it refuses the nodes the load could not place
    # (saving rewrites the dataset from what loaded, so editing one cell is what
    # deletes them) and demands a client, because a writer cannot operate
    # facade-less even on an empty dataset. Refusing here covers every browser
    # mutation at once, the way _editing covers every MCP one.
    state = await ensure_dataset_facade_for_write(dataset, session)

    return dataset, state
