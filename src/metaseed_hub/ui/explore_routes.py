"""Explore routes for Metaseed Hub.

Reuses metaseed's Explorer (merge) interface with authentication
and DatabaseSpecProvider for unified spec access.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.loader import SpecLoader
from metaseed.specs.merge import DiffVisualizer, SpecComparator
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.database import get_session

if TYPE_CHECKING:
    from metaseed_hub.models import Spec, SpecDraft

logger = logging.getLogger(__name__)


def _extract_spec_data(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Extract spec data from SpecBuilderState or raw format.

    Args:
        raw_data: The stored spec_data (may be SpecBuilderState or ProfileSpec).

    Returns:
        Dictionary containing ProfileSpec data.
    """
    if "spec" in raw_data and isinstance(raw_data["spec"], dict):
        return raw_data["spec"]
    return raw_data


class HubSpecLoader(SpecLoader):  # type: ignore[misc]
    """Extended SpecLoader that includes database specs."""

    def __init__(
        self,
        db_specs: dict[str, ProfileSpec] | None = None,
    ) -> None:
        """Initialize with optional database specs.

        Args:
            db_specs: Dict mapping "profile_key/version" to ProfileSpec.
        """
        super().__init__()
        self._db_specs = db_specs or {}

    def load_profile(
        self,
        version: str = "1.1",
        profile: str | None = None,
        *,
        ctx: Any = None,
    ) -> ProfileSpec:
        """Load profile from database or built-in specs."""
        if profile:
            key = f"{profile}/{version}"
            if key in self._db_specs:
                return self._db_specs[key]
        return super().load_profile(version, profile, ctx=ctx)


async def load_profile_spec(
    session: AsyncSession, profile_key: str, version: str
) -> tuple[str, ProfileSpec] | None:
    """Load a profile spec from built-in or database.

    Args:
        session: Database session.
        profile_key: Profile identifier (name, draft:id, or spec:id).
        version: Version string.

    Returns:
        Tuple of (display_name, ProfileSpec) or None if not found.
    """
    from metaseed_hub.models import Spec, SpecDraft

    if profile_key.startswith("draft:"):
        draft_id = profile_key[6:]
        result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = result.scalar_one_or_none()
        if draft and draft.spec_data:
            spec_data = _extract_spec_data(draft.spec_data)
            spec = ProfileSpec.model_validate(spec_data)
            return (f"{draft.name} (Draft)", spec)
        return None

    elif profile_key.startswith("spec:"):
        spec_id = profile_key[5:]
        result = await session.execute(select(Spec).where(Spec.id == spec_id))
        db_spec = result.scalar_one_or_none()
        if db_spec and db_spec.spec_data:
            spec_data = _extract_spec_data(db_spec.spec_data)
            spec = ProfileSpec.model_validate(spec_data)
            return (f"{db_spec.name} (Published)", spec)
        return None

    else:
        # Built-in profile
        loader = SpecLoader()
        try:
            spec = loader.load_profile(version, profile_key)
            return (profile_key, spec)
        except Exception:
            return None


async def load_profiles_for_comparison(
    session: AsyncSession,
    profile_specs: list[str],
) -> tuple[dict[str, ProfileSpec], list[tuple[str, str]]]:
    """Load profiles from various sources for comparison.

    Consolidates the common logic used by compare and get_diff_graph endpoints.

    Args:
        session: Database session.
        profile_specs: List of profile spec strings in "profile/version" format.

    Returns:
        Tuple of (db_specs dict, profile_tuples list).
    """
    db_specs: dict[str, ProfileSpec] = {}
    profile_tuples: list[tuple[str, str]] = []

    for spec_str in profile_specs:
        if isinstance(spec_str, str) and "/" in spec_str:
            parts = spec_str.split("/", 1)
            profile_key, version = parts[0], parts[1]
            profile_tuples.append((profile_key, version))

            if profile_key.startswith("draft:") or profile_key.startswith("spec:"):
                loaded = await load_profile_spec(session, profile_key, version)
                if loaded:
                    db_specs[f"{profile_key}/{version}"] = loaded[1]

    return db_specs, profile_tuples


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
        from metaseed_hub.models import Spec, SpecDraft, SpecStatus, Tenant, Workspace
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        try:
            # Get profiles from SpecLoader (built-in specs)
            loader = SpecLoader()
            profiles = loader.list_profiles()

            # Build profile versions and display names
            profile_versions: dict[str, list[str]] = {}
            profile_display_names: dict[str, str] = {}
            for profile in profiles:
                versions = loader.list_versions(profile)
                profile_versions[profile] = versions
                try:
                    spec = loader.load_profile(versions[0], profile)
                    profile_display_names[profile] = spec.display_name or profile
                except Exception:
                    profile_display_names[profile] = profile

            # Get user's tenant to find their workspaces
            tenant_slug = user.keycloak_id[:8]
            result = await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            tenant = result.scalar_one_or_none()

            user_drafts: list[SpecDraft] = []
            published_specs: list[Spec] = []

            if tenant:
                ws_result = await session.execute(
                    select(Workspace).where(Workspace.tenant_id == tenant.id)
                )
                workspaces = list(ws_result.scalars().all())
                workspace_ids = [ws.id for ws in workspaces]

                if workspace_ids:
                    drafts_result = await session.execute(
                        select(SpecDraft).where(SpecDraft.workspace_id.in_(workspace_ids))
                    )
                    user_drafts = list(drafts_result.scalars().all())

                    specs_result = await session.execute(
                        select(Spec).where(
                            Spec.workspace_id.in_(workspace_ids),
                            Spec.status == SpecStatus.PUBLISHED,
                        )
                    )
                    published_specs = list(specs_result.scalars().all())

            # Add user drafts to profiles
            for draft in user_drafts:
                draft_key = f"draft:{draft.id}"
                profiles.append(draft_key)
                profile_versions[draft_key] = [draft.version]
                profile_display_names[draft_key] = f"{draft.name} (Draft)"

            # Add published specs to profiles
            for spec in published_specs:
                spec_key = f"spec:{spec.id}"
                if spec_key not in profiles:
                    profiles.append(spec_key)
                    profile_versions[spec_key] = [spec.version]
                    profile_display_names[spec_key] = f"{spec.name} (Published)"

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

        except Exception as e:
            logger.exception("Explorer page failed: %s", e)
            return JSONResponse(
                {"error": f"Explorer error: {e!s}"},
                status_code=500,
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

        db_specs, profile_tuples = await load_profiles_for_comparison(
            session, [str(s) for s in profile_specs]
        )

        if len(profile_tuples) < 1:
            return JSONResponse({"error": "No valid profiles found"}, status_code=400)

        try:
            loader = HubSpecLoader(db_specs=db_specs)
            comparator = SpecComparator(loader)
            result = comparator.compare(profile_tuples)

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

        db_specs, profile_tuples = await load_profiles_for_comparison(session, profile_specs)

        try:
            loader = HubSpecLoader(db_specs=db_specs)
            comparator = SpecComparator(loader)
            result = comparator.compare(profile_tuples)
            visualizer = DiffVisualizer()
            graph_data = visualizer.build_diff_graph(result, show_unchanged=True)
            return JSONResponse(graph_data)

        except Exception as e:
            logger.exception("Graph generation failed: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    return router
