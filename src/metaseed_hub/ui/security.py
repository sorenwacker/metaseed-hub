"""Security utilities for Hub UI routes."""

import functools
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from metaseed_hub.ui.helpers import validate_csrf_token


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


def require_csrf(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that validates CSRF token before executing handler.

    For async handlers that receive a Request object as their first
    positional argument after self (if method) or first argument
    (if function).

    The decorator expects the handler to accept a 'request' parameter.
    It validates the CSRF token from cookies/headers before proceeding.

    Example:
        @router.post("/submit")
        @require_csrf
        async def submit_form(request: Request, ...):
            ...

    Raises:
        CSRFValidationError: If CSRF token is invalid or missing.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Find request in kwargs or args
        request: Request | None = kwargs.get("request")

        if request is None:
            # Look for Request in positional args
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break

        if request is None:
            raise ValueError("require_csrf: No Request object found in handler arguments")

        # Check for form_token in kwargs (for Form() parameters)
        form_token = kwargs.get("csrf_token")

        validate_csrf_or_error(request, form_token)
        return await func(*args, **kwargs)

    return wrapper
