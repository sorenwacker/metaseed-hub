"""Shared template rendering utilities for Hub UI routes."""

import asyncio
import time
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from metaseed_hub.config import get_settings
from metaseed_hub.ui.helpers import get_or_create_csrf_token, set_csrf_cookie

HUB_REPO = "sorenwacker/metaseed-hub"
_STARS_OK_TTL_SECONDS = 3600
_STARS_FAIL_TTL_SECONDS = 300
# repo -> (fetched_at_monotonic, star_count_or_none, ttl_seconds)
_stars_cache: dict[str, tuple[float, int | None, int]] = {}
# repo -> in-flight refresh task, so a stale entry spawns one refresh, not one
# per concurrent page render.
_stars_refresh_tasks: dict[str, "asyncio.Task[None]"] = {}


async def _refresh_repo_stars(repo: str) -> None:
    """Fetch the stargazer count for ``repo`` and update the cache.

    Successful results are cached for an hour; failures are cached briefly and
    keep the last known value so a transient GitHub outage does not drop the
    count from the footer.

    Args:
        repo: GitHub repository in "owner/name" form.
    """
    now = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={"Accept": "application/vnd.github+json"},
                timeout=2.0,
            )
        response.raise_for_status()
        stars = int(response.json()["stargazers_count"])
        _stars_cache[repo] = (now, stars, _STARS_OK_TTL_SECONDS)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        cached = _stars_cache.get(repo)
        prior = cached[1] if cached is not None else None
        _stars_cache[repo] = (now, prior, _STARS_FAIL_TTL_SECONDS)


def _schedule_stars_refresh(repo: str) -> None:
    """Start a background refresh for ``repo`` unless one is already running.

    A no-op outside a running event loop (e.g. a synchronous template render in
    tests), where blocking on a network call would be the only alternative.

    Args:
        repo: GitHub repository in "owner/name" form.
    """
    if repo in _stars_refresh_tasks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_refresh_repo_stars(repo))
    _stars_refresh_tasks[repo] = task
    task.add_done_callback(lambda _task: _stars_refresh_tasks.pop(repo, None))


def get_repo_stars(repo: str = HUB_REPO) -> int | None:
    """Get the cached GitHub stargazer count for a repository.

    Never performs network I/O itself: this runs inside template rendering on
    the event loop, where a blocking HTTP call would stall every concurrent
    request. A stale or missing cache entry schedules a background refresh and
    the last known value (or None) is returned immediately.

    Args:
        repo: GitHub repository in "owner/name" form.

    Returns:
        The stargazer count, or None if it has never been retrieved.
    """
    now = time.monotonic()
    cached = _stars_cache.get(repo)
    if cached is not None and now - cached[0] < cached[2]:
        return cached[1]

    _schedule_stars_refresh(repo)
    return cached[1] if cached is not None else None


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
    settings = get_settings()
    context["matomo_url"] = settings.matomo_url
    context["matomo_site_id"] = settings.matomo_site_id
    context["base_url"] = settings.app_url

    response = templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )

    # Issues a cookie on first visit and re-issues one when the stored value is
    # missing or no longer carries a valid signature.
    set_csrf_cookie(request, response, csrf_token)

    return response
