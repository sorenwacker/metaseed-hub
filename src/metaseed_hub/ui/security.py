"""Security utilities for Hub UI routes."""

from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from metaseed_hub.config import get_settings
from metaseed_hub.ui.helpers import validate_csrf_token

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_origin(request: Request) -> str:
    """Return the scheme://host the browser treats as this app's origin.

    Relies on ``request.url.scheme`` being correct behind the TLS-terminating
    proxy, which holds now that uvicorn runs with ``--proxy-headers``.
    """
    host = request.headers.get("host", request.url.netloc)
    return f"{request.url.scheme}://{host}"


async def require_same_origin(request: Request) -> None:
    """Reject cross-origin state-changing requests (Origin-based CSRF defense).

    Browsers attach an ``Origin`` header to POST/PUT/PATCH/DELETE (including
    fetch and HTMX) requests, so a cross-site forgery carries a foreign origin
    and is blocked while a genuine same-origin request matches and passes. This
    needs no per-request token, so it protects HTMX, ``fetch``, and plain-form
    submissions uniformly. When ``Origin`` is absent -- some legitimate
    same-origin requests omit it -- we defer to the SameSite=Lax cookie rather
    than break the request.

    Raises:
        HTTPException: 403 if the request carries a cross-origin ``Origin``.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {_request_origin(request)}
    parsed = urlparse(get_settings().app_url)
    if parsed.scheme and parsed.netloc:
        allowed.add(f"{parsed.scheme}://{parsed.netloc}")
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin request blocked")


class CSRFValidationError(HTTPException):
    """Raised when CSRF validation fails."""

    def __init__(self) -> None:
        super().__init__(status_code=403, detail="CSRF validation failed")


def validate_csrf_or_error(
    request: Request,
    form_token: str | None = None,
) -> None:
    """Validate CSRF token or raise HTTP 403 error.

    Args:
        request: FastAPI request object.
        form_token: Optional CSRF token from form data.

    Raises:
        CSRFValidationError: If CSRF token is invalid.
    """
    if not validate_csrf_token(request, form_token):
        raise CSRFValidationError()


def csrf_error_response() -> HTMLResponse:
    """Return HTML error response for CSRF validation failure.

    Returns:
        HTMLResponse with 403 status and error message.
    """
    return HTMLResponse(
        "<div class='error'>CSRF validation failed</div>",
        status_code=403,
    )
