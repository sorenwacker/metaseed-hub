"""Shared template rendering utilities for Hub UI routes."""

import time
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from metaseed_hub.config import get_settings
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token

HUB_REPO = "sorenwacker/metaseed-hub"
_STARS_OK_TTL_SECONDS = 3600
_STARS_FAIL_TTL_SECONDS = 300
# repo -> (fetched_at_monotonic, star_count_or_none, ttl_seconds)
_stars_cache: dict[str, tuple[float, int | None, int]] = {}


def get_repo_stars(repo: str = HUB_REPO) -> int | None:
    """Get the GitHub stargazer count for a repository, cached.

    Successful results are cached for an hour; failures are cached briefly and
    fall back to the last known value so a transient GitHub outage neither
    blocks page rendering nor drops the count from the footer.

    Args:
        repo: GitHub repository in "owner/name" form.

    Returns:
        The stargazer count, or None if it has never been retrieved.
    """
    now = time.monotonic()
    cached = _stars_cache.get(repo)
    if cached is not None and now - cached[0] < cached[2]:
        return cached[1]

    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=2.0,
        )
        response.raise_for_status()
        stars = int(response.json()["stargazers_count"])
        _stars_cache[repo] = (now, stars, _STARS_OK_TTL_SECONDS)
        return stars
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        prior = cached[1] if cached is not None else None
        _stars_cache[repo] = (now, prior, _STARS_FAIL_TTL_SECONDS)
        return prior


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

    # Set the CSRF cookie whenever it differs from the embedded token. This
    # issues a cookie on first visit and re-issues one when the stored value is
    # missing or no longer carries a valid signature.
    if request.cookies.get(CSRF_TOKEN_COOKIE) != csrf_token:
        response.set_cookie(
            key=CSRF_TOKEN_COOKIE,
            value=csrf_token,
            httponly=True,
            # Match the access-token cookie: mark Secure in every non-debug
            # deployment. Keying off request.url.scheme instead drops the flag
            # behind a TLS-terminating proxy, where the app sees http.
            secure=not get_settings().debug,
            samesite="lax",
            max_age=3600 * 24,  # 24 hours
        )

    return response
