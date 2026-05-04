"""Shared template rendering utilities for Hub UI routes."""

from functools import lru_cache
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, str]:
    """Get version information from package.

    Returns:
        Dictionary with version and commit info.
    """
    info = {"version": "dev", "short_commit": "unknown", "branch": "unknown"}
    try:
        from metaseed_hub._version import __commit_id__, __version__

        info["version"] = __version__
        if __commit_id__:
            info["short_commit"] = __commit_id__.lstrip("g")[:7]
    except ImportError:
        pass
    return info


# Module-level templates reference, set by init_templates()
_templates: Jinja2Templates | None = None


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference for shared rendering.

    Args:
        templates: Jinja2Templates instance to use for rendering.
    """
    global _templates
    _templates = templates


def get_templates() -> Jinja2Templates:
    """Get the configured templates instance.

    Returns:
        The configured Jinja2Templates instance.

    Raises:
        RuntimeError: If templates not initialized.
    """
    if _templates is None:
        raise RuntimeError("Templates not initialized. Call init_templates() first.")
    return _templates


def render_template(
    request: Request,
    name: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> Response:
    """Render template with CSRF token and standard context.

    Automatically adds CSRF token to context and sets cookie if needed.

    Args:
        request: FastAPI request object.
        name: Template name to render.
        context: Template context dictionary.
        status_code: HTTP status code for response.

    Returns:
        FastAPI Response with rendered template.

    Raises:
        RuntimeError: If templates not initialized.
    """
    templates = get_templates()

    csrf_token = get_or_create_csrf_token(request)
    context["csrf_token"] = csrf_token
    context["request"] = request
    context["version_info"] = get_version_info()

    response = templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )

    # Set CSRF cookie if not already set
    if not request.cookies.get(CSRF_TOKEN_COOKIE):
        response.set_cookie(
            key=CSRF_TOKEN_COOKIE,
            value=csrf_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=3600 * 24,  # 24 hours
        )

    return response
