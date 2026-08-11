"""Authentication routes for Hub UI."""

import logging
import secrets
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
async def auth_login(request: Request) -> RedirectResponse:
    """Redirect to OIDC provider login page."""
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
        from metaseed_hub.ui.routes.admin import record_login

        token_user = await verify_token(access_token)
        async with db.session_factory() as db_session:
            await record_login(db_session, token_user)
            landing = await _post_login_landing(db_session, token_user)
    except Exception:
        logger.exception("Could not record the sign-in")

    response = RedirectResponse(url=landing, status_code=302)
    response.delete_cookie(key=STATE_COOKIE)
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


async def refresh_access_token(refresh_token: str) -> dict[str, Any] | None:
    """Use refresh token to get new access token.

    Args:
        refresh_token: The refresh token from cookie.

    Returns:
        Dict with new tokens if successful, None if refresh failed.
    """
    settings = get_settings()
    try:
        oidc_config = await get_oidc_config()
    except HTTPException:
        return None

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
            return tokens
    except Exception as e:
        # Best-effort refresh: on any failure the caller falls back to re-login.
        # Log it so a persistently failing refresh is diagnosable.
        logger.debug(f"Token refresh failed: {e}")

    return None


@router.get("/profile")
async def auth_profile(request: Request, session: DbSession) -> Response:
    """Show the profile page with SRAM/OIDC info and account controls."""

    from metaseed_hub.repositories.account import (
        datasets_needing_new_owner,
        specs_needing_new_owner,
    )
    from metaseed_hub.ui.dependencies import (
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import get_or_create_csrf_token
    from metaseed_hub.ui.render import render_template

    user = await get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/hub/auth/login", status_code=302)

    from metaseed_hub.ui.dependencies import user_features
    from metaseed_hub.ui.services.seek_connection import connection_for_user

    _, db_user = await ensure_tenant_and_user(session, user)
    features = await user_features(request, session)
    blocking_datasets = await datasets_needing_new_owner(session, db_user)
    blocking_specs = await specs_needing_new_owner(session, db_user)

    return render_template(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "nav_active": "profile",
            "features": features,
            "seek_error": request.query_params.get("seek_error"),
            # The SEEK connection lives here, with the other per-user
            # credentials, rather than on a page of its own that nothing links to.
            "connection": (
                await connection_for_user(session, user) if "seek" in features else None
            ),
            "csrf_token": get_or_create_csrf_token(request),
            "datasets_needing_new_owner": blocking_datasets,
            "specs_needing_new_owner": blocking_specs,
            "delete_error": request.query_params.get("error"),
            "api_tokens": await active_tokens(session, db_user),
            # Shown once, immediately after minting, and never retrievable again.
            "new_token": request.query_params.get("token"),
        },
    )


@router.post("/profile/tokens")
async def auth_create_token(
    request: Request,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    """Mint a personal access token for the signed-in user."""
    from metaseed_hub.tokens import issue_token
    from metaseed_hub.ui.dependencies import (
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import validate_csrf_token

    user = await get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/hub/auth/login", status_code=302)
    submitted = (await request.form()).get("_csrf_token")
    if not validate_csrf_token(request, submitted if isinstance(submitted, str) else None):
        return RedirectResponse(url="/hub/auth/profile?error=csrf", status_code=302)

    _, db_user = await ensure_tenant_and_user(session, user)
    secret, _token = await issue_token(session, db_user, name=name.strip() or "token")

    # Carried back once so the page can show it; it is not stored anywhere.
    return RedirectResponse(url=f"/hub/auth/profile?token={secret}", status_code=303)


@router.post("/profile/tokens/{token_id}/revoke")
async def auth_revoke_token(
    request: Request,
    token_id: str,
    session: DbSession,
) -> Response:
    """Withdraw one of the signed-in user's tokens."""
    from metaseed_hub.tokens import revoke_token
    from metaseed_hub.ui.dependencies import (
        ensure_tenant_and_user,
        get_current_user_from_cookie,
    )
    from metaseed_hub.ui.helpers import validate_csrf_token

    user = await get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/hub/auth/login", status_code=302)
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
        return RedirectResponse(url="/hub/auth/login", status_code=302)

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
