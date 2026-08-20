"""Authentication routes for Hub UI."""

import logging
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.auth import TokenUser

from metaseed_hub.config import get_settings
from metaseed_hub.models import ApiToken
from metaseed_hub.tokens import active_tokens

# ACCESS_TOKEN_COOKIE is single-sourced in ui.dependencies and re-exported here
# for the cookie writers (ui.app middleware) that import it from this module.
from metaseed_hub.ui.dependencies import ACCESS_TOKEN_COOKIE as ACCESS_TOKEN_COOKIE
from metaseed_hub.ui.dependencies import DbSession

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie names (ACCESS_TOKEN_COOKIE is single-sourced in ui.dependencies)
REFRESH_TOKEN_COOKIE = "metaseed_refresh_token"
STATE_COOKIE = "metaseed_oauth_state"

# Refresh token lifetime (30 days)
REFRESH_TOKEN_MAX_AGE = 30 * 24 * 60 * 60

NEXT_COOKIE = "metaseed_oauth_next"
"""Carries the page a refused request was headed for across the sign-in.

A query parameter cannot do it: the callback URL is registered with the
identity provider and has to match exactly. Lives as long as the state cookie,
and is deleted by the callback that reads it.
"""

NEXT_COOKIE_MAX_AGE = 600


def _next_after_login(candidate: str | None, *, default: str) -> str:
    """The page to land on after signing in, or ``default`` if it is not ours.

    ``candidate`` originates in a query parameter, so it is attacker-writable:
    accepted only as an absolute path on this hub, which rules out an absolute
    URL, a protocol-relative ``//host`` (which a browser reads as another
    origin), and a traversal out of the mount.

    Args:
        candidate: The requested destination, as received.
        default: Where to go when the request named nowhere usable.

    Returns:
        A path on this hub.
    """
    if not candidate or not candidate.startswith("/hub"):
        return default
    if candidate.startswith("//") or ".." in candidate:
        return default
    return candidate


# OIDC discovery cache
_oidc_config: dict[str, Any] | None = None


async def get_oidc_config() -> dict[str, Any]:
    """Fetch and cache OIDC discovery configuration.

    Raises:
        HTTPException: If OIDC provider is unreachable or misconfigured.
    """
    global _oidc_config
    if _oidc_config is not None:
        return _oidc_config

    settings = get_settings()
    discovery_url = settings.oidc_discovery_url

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            _oidc_config = response.json()
            return _oidc_config
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=503,
            detail=f"OIDC discovery failed: {e.response.status_code} from {discovery_url}",
        )
    except httpx.HTTPError:
        # Covers connect errors, timeouts, and all other transport failures.
        raise HTTPException(
            status_code=503,
            detail=f"OIDC provider not reachable at {settings.effective_issuer}",
        )


@router.get("/login")
async def auth_login(request: Request, next: str | None = None) -> RedirectResponse:
    """Redirect to OIDC provider login page.

    Args:
        request: The incoming request.
        next: The page the user was refused, remembered across the sign-in so
            an expired session does not also cost them their place. Ignored
            unless it is a path on this hub.
    """
    settings = get_settings()
    hub_base_url = f"{settings.app_url}/hub"
    oidc_config = await get_oidc_config()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"{hub_base_url}/auth/callback"

    params = {
        "client_id": settings.effective_client_id,
        "response_type": "code",
        "scope": settings.oidc_scope,
        "redirect_uri": redirect_uri,
        "state": state,
        "prompt": "consent",
    }

    auth_url = f"{oidc_config['authorization_endpoint']}?{urlencode(params)}"

    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key=STATE_COOKIE,
        value=state,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=600,
    )
    destination = _next_after_login(next, default="")
    if destination:
        response.set_cookie(
            key=NEXT_COOKIE,
            value=destination,
            httponly=True,
            secure=not settings.debug,
            samesite="lax",
            max_age=NEXT_COOKIE_MAX_AGE,
            path="/hub",
        )
    return response


async def _post_login_landing(session: "AsyncSession", token_user: "TokenUser") -> str:
    """Where to send a user after sign-in.

    A user with no datasets or specifications lands on the Home guide, which
    explains what to do; a returning user with work lands on their datasets. The
    guide is otherwise reached only from the logo, so the person who most needs
    it -- someone signing in for the first time -- would never see it.
    """
    from sqlalchemy import func, select

    from metaseed_hub.models import Dataset, Spec, Tenant
    from metaseed_hub.ui.dependencies import tenant_slug_for

    slug = tenant_slug_for(token_user.sub)
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        return "/hub/home"  # brand new: no account yet, so no content

    datasets = await session.scalar(
        select(func.count(Dataset.id)).where(
            Dataset.tenant_id == tenant.id, Dataset.deleted_at.is_(None)
        )
    )
    specs = await session.scalar(
        select(func.count(Spec.id)).where(Spec.tenant_id == tenant.id, Spec.deleted_at.is_(None))
    )
    return "/hub/" if (datasets or specs) else "/hub/home"


async def _after_sign_in(session: "AsyncSession", token_user: "TokenUser") -> str:
    """Record the sign-in, make sure the account exists, and say where to go.

    Provisioning belongs here rather than on whichever page the user reaches
    first. A newcomer is sent to the Home guide precisely because they have no
    account yet, and that page renders without creating one — so someone could
    sign in, read it, and remain unresolvable to sharing, which finds people by
    the address on their account.

    Args:
        session: Database session.
        token_user: The person who just signed in.

    Returns:
        The path to redirect them to.
    """
    from metaseed_hub.ui.dependencies import ensure_tenant_and_user
    from metaseed_hub.ui.routes.admin import record_login

    # Provision before stamping: record_login needs a row to write to, and for
    # a first-time user there is none until this runs, so their very first
    # sign-in went unrecorded and the admin directory read "Never".
    await ensure_tenant_and_user(session, token_user)
    await record_login(session, token_user)
    return await _post_login_landing(session, token_user)


@router.get("/callback")
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle OAuth callback from OIDC provider."""
    if error:
        return RedirectResponse(url="/hub/?error=auth_failed", status_code=302)

    stored_state = request.cookies.get(STATE_COOKIE)
    if not state or state != stored_state:
        return RedirectResponse(url="/hub/?error=invalid_state", status_code=302)

    if not code:
        return RedirectResponse(url="/hub/?error=no_code", status_code=302)

    # Exchange code for tokens
    settings = get_settings()
    hub_base_url = f"{settings.app_url}/hub"
    oidc_config = await get_oidc_config()
    redirect_uri = f"{hub_base_url}/auth/callback"

    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                oidc_config["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.effective_client_id,
                    "client_secret": settings.effective_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=10.0,
            )
    except httpx.HTTPError:
        # An IdP outage mid sign-in must produce the same friendly redirect as
        # a non-200 response, not an unhandled 500.
        logger.exception("Token exchange with the OIDC provider failed")
        return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)

    if token_response.status_code != 200:
        return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)

    tokens = token_response.json()
    access_token = tokens.get("access_token")
    if not access_token:
        # A 200 response without an access token (malformed/non-compliant IdP)
        # would otherwise set a cookie of literal "None" and a broken session.
        return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)
    refresh_token = tokens.get("refresh_token")

    # A returning user with work lands on it; a user with nothing lands on the
    # Home guide. Defaulted here so a failure below still redirects.
    landing = "/hub/"

    # Record the sign-in for the admin dashboard. Here rather than on each
    # request, so the column means "last signed in" and not "last seen"; it
    # never blocks the redirect, so a bookkeeping failure cannot lock a user out.
    try:
        from metaseed_hub.auth import verify_token
        from metaseed_hub.database import db

        token_user = await verify_token(access_token)
        async with db.session_factory() as db_session:
            landing = await _after_sign_in(db_session, token_user)
    except Exception:
        # Never block the redirect: a bookkeeping or provisioning failure must
        # not lock a user out, and the next authenticated page retries both.
        logger.exception("Could not complete the sign-in bookkeeping")

    # Where the user was going when the session ran out beats the default
    # landing: they clicked a dataset, not the dataset list.
    landing = _next_after_login(request.cookies.get(NEXT_COOKIE), default=landing)

    response = RedirectResponse(url=landing, status_code=302)
    response.delete_cookie(key=STATE_COOKIE)
    response.delete_cookie(key=NEXT_COOKIE, path="/hub")
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=tokens.get("expires_in", 3600),
        path="/",
    )
    # Store refresh token for longer sessions
    if refresh_token:
        response.set_cookie(
            key=REFRESH_TOKEN_COOKIE,
            value=refresh_token,
            httponly=True,
            secure=not settings.debug,
            samesite="lax",
            max_age=REFRESH_TOKEN_MAX_AGE,
            path="/",
        )
    return response


@dataclass(frozen=True)
class RefreshResult:
    """What the issuer said when asked to renew a session.

    ``rejected`` separates a verdict from an outage: the issuer answering "this
    refresh token is no good" ends the session and clears the cookies, while an
    unreachable issuer is not checked at all and leaves them alone. Conflating
    the two would sign every user out for the length of someone else's downtime.

    Attributes:
        tokens: The new token set, or None if none was issued.
        rejected: True only when the issuer explicitly refused the token.
    """

    tokens: dict[str, Any] | None
    rejected: bool


async def refresh_access_token(refresh_token: str) -> RefreshResult:
    """Use refresh token to get new access token.

    Args:
        refresh_token: The refresh token from cookie.

    Returns:
        The new tokens, and whether the issuer refused the refresh token.
    """
    settings = get_settings()
    try:
        oidc_config = await get_oidc_config()
    except HTTPException:
        return RefreshResult(tokens=None, rejected=False)

    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                oidc_config["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.effective_client_id,
                    "client_secret": settings.effective_client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=10.0,
            )

        if token_response.status_code == 200:
            tokens: dict[str, Any] = token_response.json()
            return RefreshResult(tokens=tokens, rejected=False)
        # 400/401 is the issuer saying the token is expired, revoked, or
        # already used (OAuth 2.0 ``invalid_grant``). Anything else -- a 500,
        # a gateway error -- is the issuer having a bad day, not a verdict.
        if token_response.status_code in (400, 401):
            return RefreshResult(tokens=None, rejected=True)
    except Exception as e:
        # Best-effort refresh: on any failure the caller falls back to re-login.
        # Log it so a persistently failing refresh is diagnosable.
        logger.debug(f"Token refresh failed: {e}")

    return RefreshResult(tokens=None, rejected=False)


@router.get("/profile")
async def auth_profile(request: Request, session: DbSession) -> Response:
    """Show the profile page with SRAM/OIDC info and account controls."""

    from metaseed_hub.repositories.account import (
        datasets_needing_new_owner,
        specs_needing_new_owner,
    )
    from metaseed_hub.ui.dependencies import (
        AuthRequiredError,
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import get_or_create_csrf_token
    from metaseed_hub.ui.render import render_template

    user = await get_current_user_from_cookie(request)
    if not user:
        raise AuthRequiredError()

    from metaseed_hub.ui.services.seek_connection import connection_for_user

    _, db_user = await ensure_tenant_and_user(session, user)
    blocking_datasets = await datasets_needing_new_owner(session, db_user)
    blocking_specs = await specs_needing_new_owner(session, db_user)

    # The one-shot cookie set by auth_create_token. Expiry is the cookie's
    # max_age; a decrypt failure (rotated SECRET_KEY) just shows no token.
    from metaseed_hub.crypto import decrypt_secret

    stored = request.cookies.get(NEW_TOKEN_COOKIE)
    new_token = decrypt_secret(stored) if stored else None

    response = render_template(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "nav_active": "profile",
            "seek_error": request.query_params.get("seek_error"),
            # The SEEK connection lives here, with the other per-user
            # credentials, rather than on a page of its own that nothing links to.
            "connection": (await connection_for_user(session, user)),
            "csrf_token": get_or_create_csrf_token(request),
            "datasets_needing_new_owner": blocking_datasets,
            "specs_needing_new_owner": blocking_specs,
            "delete_error": request.query_params.get("error"),
            "api_tokens": await active_tokens(session, db_user),
            # Shown once, immediately after minting, and never retrievable
            # again: read from the one-shot cookie and expired below.
            "new_token": new_token,
        },
    )
    response.delete_cookie(NEW_TOKEN_COOKIE, path="/hub/auth/profile")
    return response


NEW_TOKEN_COOKIE = "hub_new_token"
"""One-shot cookie carrying a freshly minted token secret to the profile page.

A redirect query parameter would put the live credential into the access log
and the browser history. The cookie is Fernet-encrypted, its ``max_age`` is a
minute, and the profile page deletes it on first read.
"""

NEW_TOKEN_TTL_SECONDS = 60


@router.post("/profile/tokens")
async def auth_create_token(
    request: Request,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    """Mint a personal access token for the signed-in user."""
    from metaseed_hub.tokens import issue_token
    from metaseed_hub.ui.dependencies import (
        AuthRequiredError,
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import validate_csrf_token

    user = await get_current_user_from_cookie(request)
    if not user:
        raise AuthRequiredError()
    submitted = (await request.form()).get("_csrf_token")
    if not validate_csrf_token(request, submitted if isinstance(submitted, str) else None):
        return RedirectResponse(url="/hub/auth/profile?error=csrf", status_code=302)

    _, db_user = await ensure_tenant_and_user(session, user)
    secret, _token = await issue_token(session, db_user, name=name.strip() or "token")

    # Carried back once so the page can show it — in a short-lived encrypted
    # cookie, never in the URL, which lands in access logs and history.
    from metaseed_hub.crypto import encrypt_secret

    response = RedirectResponse(url="/hub/auth/profile", status_code=303)
    response.set_cookie(
        NEW_TOKEN_COOKIE,
        encrypt_secret(secret),
        max_age=NEW_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=not get_settings().debug,
        samesite="lax",
        path="/hub/auth/profile",
    )
    return response


@router.post("/profile/tokens/{token_id}/revoke")
async def auth_revoke_token(
    request: Request,
    token_id: str,
    session: DbSession,
) -> Response:
    """Withdraw one of the signed-in user's tokens."""
    from metaseed_hub.tokens import revoke_token
    from metaseed_hub.ui.dependencies import (
        AuthRequiredError,
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import validate_csrf_token

    user = await get_current_user_from_cookie(request)
    if not user:
        raise AuthRequiredError()
    submitted = (await request.form()).get("_csrf_token")
    if not validate_csrf_token(request, submitted if isinstance(submitted, str) else None):
        return RedirectResponse(url="/hub/auth/profile?error=csrf", status_code=302)

    _, db_user = await ensure_tenant_and_user(session, user)
    token = await session.get(ApiToken, token_id)
    # Scoped to the caller: a token id is guessable, and revoking someone else's
    # would be a denial of service against them.
    if token is not None and token.user_id == db_user.id:
        await revoke_token(session, token)

    return RedirectResponse(url="/hub/auth/profile", status_code=303)


@router.post("/profile/delete")
async def auth_delete_account(request: Request, session: DbSession) -> Response:
    """Permanently delete the signed-in user's account (GDPR right to erasure).

    Refuses while the user solely owns a dataset -- those must first be
    reassigned to a new owner or deleted. Requires the CSRF token and a typed
    email confirmation to guard against accidental deletion.
    """
    from metaseed_hub.repositories.account import (
        AccountDeletionBlockedError,
        delete_account,
    )
    from metaseed_hub.ui.dependencies import (
        AuthRequiredError,
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.security import (
        CSRFValidationError,
        csrf_error_response,
        validate_csrf_or_error,
    )

    user = await get_current_user_from_cookie(request)
    if not user:
        raise AuthRequiredError()

    form = await request.form()
    csrf_token = form.get("_csrf_token")
    try:
        validate_csrf_or_error(request, csrf_token if isinstance(csrf_token, str) else None)
    except CSRFValidationError:
        return csrf_error_response()

    if (str(form.get("confirm_email") or "")).strip() != user.email:
        return RedirectResponse(url="/hub/auth/profile?error=confirm", status_code=302)

    _, db_user = await ensure_tenant_and_user(session, user)
    try:
        await delete_account(session, db_user)
    except AccountDeletionBlockedError:
        await session.rollback()
        return RedirectResponse(url="/hub/auth/profile?error=owned_datasets", status_code=302)
    await session.commit()

    response = RedirectResponse(url="/hub/", status_code=302)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE)
    response.delete_cookie(key=REFRESH_TOKEN_COOKIE)
    return response


@router.get("/logout")
async def auth_logout(request: Request) -> RedirectResponse:
    """Logout and redirect to OIDC provider logout."""
    settings = get_settings()
    hub_base_url = f"{settings.app_url}/hub"
    oidc_config = await get_oidc_config()
    logout_url = oidc_config.get("end_session_endpoint")

    response = RedirectResponse(url="/hub/", status_code=302)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE)
    response.delete_cookie(key=REFRESH_TOKEN_COOKIE)

    if logout_url:
        params = {
            "client_id": settings.effective_client_id,
            "post_logout_redirect_uri": hub_base_url,
        }
        response = RedirectResponse(url=f"{logout_url}?{urlencode(params)}", status_code=302)
        response.delete_cookie(key=ACCESS_TOKEN_COOKIE)
        response.delete_cookie(key=REFRESH_TOKEN_COOKIE)

    return response
