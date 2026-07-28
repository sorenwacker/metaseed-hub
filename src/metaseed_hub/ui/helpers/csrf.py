"""CSRF token signing and validation (signed double-submit cookie)."""

import hashlib
import hmac
import secrets

from fastapi import Request, Response

from metaseed_hub.config import get_settings

CSRF_TOKEN_COOKIE = "metaseed_csrf_token"


def _sign_csrf(token: str) -> str:
    """Return the token with an HMAC signature keyed by the application secret.

    Signing lets the server recognise tokens it issued, so an attacker cannot
    fixate or forge the CSRF cookie without knowing ``secret_key``.

    Args:
        token: The random CSRF token to sign.

    Returns:
        The value ``"<token>.<hex-signature>"`` stored in the cookie and form.
    """
    secret = get_settings().secret_key.encode()
    signature = hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def _csrf_signature_valid(signed: str) -> bool:
    """Return True if a signed CSRF value carries a valid signature.

    Args:
        signed: A ``"<token>.<signature>"`` value from a cookie or form.

    Returns:
        True when the signature matches the application secret.
    """
    token, _, signature = signed.rpartition(".")
    if not token or not signature:
        return False
    secret = get_settings().secret_key.encode()
    expected = hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_or_create_csrf_token(request: Request) -> str:
    """Return the request's signed CSRF token, issuing a new one if needed.

    Args:
        request: The request object.

    Returns:
        A signed CSRF token to embed in the page and set as a cookie.
    """
    token = request.cookies.get(CSRF_TOKEN_COOKIE)
    if token and _csrf_signature_valid(token):
        return token
    return _sign_csrf(secrets.token_urlsafe(32))


def validate_csrf_token(request: Request, form_token: str | None = None) -> bool:
    """Validate the submitted CSRF token against the signed cookie.

    The cookie value must carry a valid application signature and match the
    token submitted in the header or form (double-submit).

    Args:
        request: The request object.
        form_token: Optional CSRF token from form data.

    Returns:
        True if the token is present, signed, and matches; False otherwise.
    """
    cookie_token = request.cookies.get(CSRF_TOKEN_COOKIE)
    # Check header first (for AJAX requests), then form data
    token = request.headers.get("X-CSRF-Token") or form_token

    if not cookie_token or not token:
        return False

    if not _csrf_signature_valid(cookie_token):
        return False

    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(cookie_token, token)


def set_csrf_cookie(request: Request, response: Response, token: str) -> None:
    """Issue the CSRF cookie when it differs from the token embedded in the page.

    The double-submit check compares the form field against this cookie, so any
    renderer that embeds a token must also make sure the cookie carries it. One
    helper rather than a copy per renderer, because the two must agree: a page
    rendered with a token the cookie does not match has every form rejected.

    Args:
        request: The incoming request, for the cookie already presented.
        response: The response to set the cookie on.
        token: The signed token embedded in the rendered page.
    """
    if request.cookies.get(CSRF_TOKEN_COOKIE) == token:
        return
    response.set_cookie(
        key=CSRF_TOKEN_COOKIE,
        value=token,
        httponly=True,
        # Match the access-token cookie: mark Secure in every non-debug
        # deployment. Keying off request.url.scheme instead drops the flag
        # behind a TLS-terminating proxy, where the app sees http.
        secure=not get_settings().debug,
        samesite="lax",
        max_age=3600 * 24,  # 24 hours
    )
