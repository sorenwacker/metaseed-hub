"""Authentication routes for Hub UI."""

import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from metaseed_hub.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie names
ACCESS_TOKEN_COOKIE = "metaseed_access_token"
REFRESH_TOKEN_COOKIE = "metaseed_refresh_token"
STATE_COOKIE = "metaseed_oauth_state"

# Refresh token lifetime (30 days)
REFRESH_TOKEN_MAX_AGE = 30 * 24 * 60 * 60

# OIDC discovery cache
_oidc_config: dict[str, str] | None = None


async def get_oidc_config() -> dict[str, str]:
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
            response = await client.get(discovery_url)
            response.raise_for_status()
            _oidc_config = response.json()
            return _oidc_config
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"OIDC provider not reachable at {settings.effective_issuer}",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=503,
            detail=f"OIDC discovery failed: {e.response.status_code} from {discovery_url}",
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
        "scope": "openid email profile offline_access",
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
        )

    if token_response.status_code != 200:
        return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)

    tokens = token_response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    response = RedirectResponse(url="/hub/", status_code=302)
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
    except Exception:
        pass

    return None


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
