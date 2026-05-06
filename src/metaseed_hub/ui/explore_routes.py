"""Explore routes for Metaseed Hub.

Reuses metaseed's Explorer (merge) interface with authentication
and DatabaseSpecProvider for unified spec access.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.loader import SpecLoader
from metaseed.specs.merge import DiffVisualizer, compare
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.database import get_session

logger = logging.getLogger(__name__)


def create_explore_router(templates: Jinja2Templates) -> APIRouter:
    """Create the explore router with routes.

    Uses metaseed's merge/index.html template for consistency.

    Args:
        templates: Jinja2Templates instance (must include metaseed templates).

    Returns:
        Configured APIRouter.
    """
    from metaseed_hub.ui.app import get_version_info
    from metaseed_hub.ui.helpers import get_or_create_csrf_token

    router = APIRouter(prefix="/explore", tags=["explore"])

    def render(request: Request, template: str, context: dict[str, Any]) -> Response:
        """Render template with version info, nav_active, and csrf_token."""
        context["version_info"] = get_version_info()
        context["nav_active"] = "explore"
        context["csrf_token"] = get_or_create_csrf_token(request)
        context["request"] = request
        return templates.TemplateResponse(request, template, context)

    @router.get("/", response_model=None)
    async def explore_index(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Explorer page - reuses metaseed's merge interface."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Get profiles from SpecLoader (built-in specs)
        loader = SpecLoader()
        profiles = loader.list_profiles()

        # Build profile versions and display names
        profile_versions = {}
        profile_display_names = {}
        for profile in profiles:
            versions = loader.list_versions(profile)
            profile_versions[profile] = versions
            try:
                spec = loader.load_profile(versions[0], profile)
                profile_display_names[profile] = spec.display_name or profile
            except Exception:
                profile_display_names[profile] = profile

        # Use metaseed's explore template with hub base_url
        return render(
            request,
            "explore/index.html",
            {
                "base_url": "/hub",
                "profiles": profiles,
                "profile_versions": profile_versions,
                "profile_display_names": profile_display_names,
                "user": user,
            },
        )

    @router.post("/compare", response_model=None)
    async def explore_compare(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> JSONResponse:
        """Compare/explore profiles - matches metaseed's API."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return JSONResponse({"error": "Login required"}, status_code=401)

        form = await request.form()
        profile_specs = form.getlist("profiles")

        if len(profile_specs) < 1:
            return JSONResponse({"error": "Select at least 1 profile"}, status_code=400)

        # Parse profile specs (format: "profile/version")
        profile_tuples = []
        for spec in profile_specs:
            if isinstance(spec, str) and "/" in spec:
                parts = spec.split("/", 1)
                profile_tuples.append((parts[0], parts[1]))

        try:
            result = compare(profile_tuples)
            visualizer = DiffVisualizer()
            graph_data = visualizer.build_diff_graph(result, show_unchanged=True)

            return JSONResponse(
                {
                    "success": True,
                    "graph": graph_data,
                    "statistics": {
                        "profiles": result.profiles,
                        "total_entities": result.statistics.total_entities,
                        "common_entities": result.statistics.common_entities,
                        "unique_entities": result.statistics.unique_entities,
                        "modified_entities": result.statistics.modified_entities,
                        "total_fields": result.statistics.total_fields,
                        "common_fields": result.statistics.common_fields,
                        "conflicting_fields": result.statistics.conflicting_fields,
                    },
                }
            )

        except Exception as e:
            logger.exception("Compare failed: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/graph/{profiles:path}", response_model=None)
    async def get_diff_graph(
        request: Request,
        profiles: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> JSONResponse:
        """Get diff visualization data - matches metaseed's API."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return JSONResponse({"error": "Login required"}, status_code=401)

        profile_specs = profiles.split(",")

        if len(profile_specs) < 1:
            return JSONResponse({"error": "At least 1 profile required"}, status_code=400)

        profile_tuples = []
        for spec in profile_specs:
            if "/" in spec:
                parts = spec.split("/", 1)
                profile_tuples.append((parts[0], parts[1]))

        try:
            result = compare(profile_tuples)
            visualizer = DiffVisualizer()
            graph_data = visualizer.build_diff_graph(result, show_unchanged=True)
            return JSONResponse(graph_data)

        except Exception as e:
            logger.exception("Graph generation failed: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    return router
