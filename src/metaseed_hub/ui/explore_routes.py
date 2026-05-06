"""Explore routes for Metaseed Hub.

Provides FastAPI routes for browsing published specifications
and comparing them side-by-side. Uses DatabaseSpecProvider for
unified access to built-in and database specs.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.merge import DiffVisualizer, compare
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.database import get_session

from .spec_adapters import DatabaseSpecProvider

logger = logging.getLogger(__name__)


def create_explore_router(templates: Jinja2Templates) -> APIRouter:
    """Create the explore router with routes.

    Args:
        templates: Jinja2Templates instance.

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

    @router.get("", response_model=None)
    async def explore_index(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """List all published specs for browsing."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        provider = DatabaseSpecProvider(session)

        # Get all profiles with their versions
        profiles = await provider.list_profiles()
        builtin_specs = []
        db_specs = []

        for profile in profiles:
            versions = await provider.list_versions(profile)
            display_name = await provider.get_display_name(profile)

            for version in versions:
                try:
                    spec = await provider.get_spec(profile, version)
                    spec_info = {
                        "id": f"{profile}:{version}",
                        "name": spec.name or profile,
                        "display_name": spec.display_name or display_name,
                        "version": version,
                        "description": spec.description or "",
                        "profile": profile,
                        "entity_count": len(spec.entities) if spec.entities else 0,
                    }

                    # Check if it's a built-in spec
                    if profile in {"miappe", "isa", "dissco", "darwin-core"}:
                        spec_info["is_builtin"] = True
                        builtin_specs.append(spec_info)
                    else:
                        spec_info["is_builtin"] = False
                        db_specs.append(spec_info)
                except Exception as e:
                    logger.warning("Failed to load spec %s/%s: %s", profile, version, e)

        return render(
            request,
            "explore/index.html",
            {
                "specs": db_specs,
                "builtin_specs": builtin_specs,
                "user": user,
            },
        )

    @router.get("/spec/{profile}/{version}", response_model=None)
    async def explore_view_spec(
        request: Request,
        profile: str,
        version: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """View a spec in read-only mode."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        provider = DatabaseSpecProvider(session)

        try:
            spec = await provider.get_spec(profile, version)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Spec not found")

        is_builtin = profile in {"miappe", "isa", "dissco", "darwin-core"}

        return render(
            request,
            "explore/view_builtin.html",
            {
                "spec": spec,
                "profile": profile,
                "version": version,
                "is_builtin": is_builtin,
                "user": user,
            },
        )

    @router.get("/builtin/{profile}/{version}", response_model=None)
    async def explore_view_builtin_spec(
        request: Request,
        profile: str,
        version: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """View a built-in spec in read-only mode (legacy route)."""
        # Redirect to unified route
        return RedirectResponse(
            url=f"/hub/explore/spec/{profile}/{version}",
            status_code=302,
        )

    @router.get("/compare", response_model=None)
    async def explore_compare(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        spec_a: str | None = Query(None, description="First spec (profile/version)"),
        spec_b: str | None = Query(None, description="Second spec (profile/version)"),
    ) -> Response:
        """Compare two specs side-by-side."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        provider = DatabaseSpecProvider(session)

        # Get all profiles for the selector dropdowns
        profiles = await provider.list_profiles()
        all_specs = []
        for profile in profiles:
            versions = await provider.list_versions(profile)
            display_name = await provider.get_display_name(profile)
            for version in versions:
                all_specs.append(
                    {
                        "id": f"{profile}/{version}",
                        "name": display_name,
                        "version": version,
                        "profile": profile,
                    }
                )

        # Load selected specs and run comparison
        selected_spec_a = None
        selected_spec_b = None
        comparison_result = None
        diff_graph = None

        if spec_a:
            try:
                parts = spec_a.split("/", 1)
                if len(parts) == 2:
                    spec = await provider.get_spec(parts[0], parts[1])
                    selected_spec_a = {
                        "name": spec.display_name or parts[0],
                        "version": parts[1],
                        "profile": parts[0],
                    }
            except Exception as e:
                logger.warning("Failed to load spec_a %s: %s", spec_a, e)

        if spec_b:
            try:
                parts = spec_b.split("/", 1)
                if len(parts) == 2:
                    spec = await provider.get_spec(parts[0], parts[1])
                    selected_spec_b = {
                        "name": spec.display_name or parts[0],
                        "version": parts[1],
                        "profile": parts[0],
                    }
            except Exception as e:
                logger.warning("Failed to load spec_b %s: %s", spec_b, e)

        # Run comparison if both specs selected
        if selected_spec_a and selected_spec_b:
            try:
                # Use metaseed's compare function
                result = compare(
                    [
                        (selected_spec_a["profile"], selected_spec_a["version"]),
                        (selected_spec_b["profile"], selected_spec_b["version"]),
                    ]
                )

                visualizer = DiffVisualizer(result)
                diff_graph = visualizer.build_diff_graph(show_unchanged=True)

                comparison_result = {
                    "total_entities": result.statistics.total_entities,
                    "added_entities": result.statistics.added_entities,
                    "removed_entities": result.statistics.removed_entities,
                    "modified_entities": result.statistics.modified_entities,
                    "unchanged_entities": result.statistics.unchanged_entities,
                    "total_fields": result.statistics.total_fields,
                    "added_fields": result.statistics.added_fields,
                    "removed_fields": result.statistics.removed_fields,
                    "modified_fields": result.statistics.modified_fields,
                    "unchanged_fields": result.statistics.unchanged_fields,
                }
            except Exception as e:
                logger.exception("Comparison failed: %s", e)
                comparison_result = {"error": str(e)}

        return render(
            request,
            "explore/compare.html",
            {
                "specs": all_specs,
                "selected_spec_a": selected_spec_a,
                "selected_spec_b": selected_spec_b,
                "spec_a_id": spec_a,
                "spec_b_id": spec_b,
                "comparison_result": comparison_result,
                "diff_graph": diff_graph,
                "user": user,
            },
        )

    @router.get("/compare/api", response_model=None)
    async def explore_compare_api(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        spec_a: str = Query(..., description="First spec (profile/version)"),
        spec_b: str = Query(..., description="Second spec (profile/version)"),
    ) -> JSONResponse:
        """API endpoint to get comparison diff graph data."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            raise HTTPException(status_code=401, detail="Login required")

        try:
            parts_a = spec_a.split("/", 1)
            parts_b = spec_b.split("/", 1)

            if len(parts_a) != 2 or len(parts_b) != 2:
                raise HTTPException(status_code=400, detail="Invalid spec format")

            result = compare(
                [
                    (parts_a[0], parts_a[1]),
                    (parts_b[0], parts_b[1]),
                ]
            )

            visualizer = DiffVisualizer(result)
            diff_graph = visualizer.build_diff_graph(show_unchanged=True)

            return JSONResponse(content=diff_graph)

        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Spec not found")
        except Exception as e:
            logger.exception("Comparison failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return router
