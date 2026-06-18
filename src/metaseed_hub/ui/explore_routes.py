"""Explore routes for Metaseed Hub.

Reuses metaseed's Explorer (merge) interface with authentication and
tenant-scoped loading of database-backed specs.
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
    session: AsyncSession, profile_key: str, version: str, tenant_id: str | None
) -> tuple[str, ProfileSpec] | None:
    """Load a profile spec from built-in or database.

    Database-backed drafts and published specs are scoped to ``tenant_id`` so a
    caller cannot load specs belonging to another tenant. Built-in profiles are
    tenant-independent.

    Args:
        session: Database session.
        profile_key: Profile identifier (name, draft:id, or spec:id).
        version: Version string.
        tenant_id: Caller's tenant; database specs are only returned when they
            belong to this tenant. ``None`` means no database spec is accessible.

    Returns:
        Tuple of (display_name, ProfileSpec) or None if not found or not
        accessible to the caller's tenant.
    """
    from metaseed_hub.models import Spec, SpecDraft

    if profile_key.startswith("draft:"):
        if tenant_id is None:
            return None
        draft_id = profile_key[6:]
        result = await session.execute(
            select(SpecDraft).where(
                SpecDraft.id == draft_id,
                SpecDraft.tenant_id == tenant_id,
            )
        )
        draft = result.scalar_one_or_none()
        if draft and draft.spec_data:
            spec_data = _extract_spec_data(draft.spec_data)
            spec = ProfileSpec.model_validate(spec_data)
            return (f"{draft.name} (Draft)", spec)
        return None

    elif profile_key.startswith("spec:"):
        if tenant_id is None:
            return None
        spec_id = profile_key[5:]
        result = await session.execute(
            select(Spec).where(
                Spec.id == spec_id,
                Spec.tenant_id == tenant_id,
                Spec.deleted_at.is_(None),
            )
        )
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
    tenant_id: str | None,
) -> tuple[dict[str, ProfileSpec], list[tuple[str, str]]]:
    """Load profiles from various sources for comparison.

    Consolidates the common logic used by compare and get_diff_graph endpoints.
    Database-backed specs are scoped to the caller's tenant.

    Args:
        session: Database session.
        profile_specs: List of profile spec strings in "profile/version" format.
        tenant_id: Caller's tenant used to scope database specs; ``None`` means
            no database spec is accessible.

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
                loaded = await load_profile_spec(session, profile_key, version, tenant_id)
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
        from metaseed_hub.models import (
            Spec,
            SpecDraft,
            SpecDraftMember,
            SpecStatus,
            Tenant,
            User,
        )
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

            # Get user's tenant
            tenant_slug = user.keycloak_id[:8]
            result = await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            tenant = result.scalar_one_or_none()

            user_drafts: list[SpecDraft] = []
            published_specs: list[Spec] = []

            # Get user's database record for membership check
            db_user_result = await session.execute(select(User).where(User.keycloak_id == user.sub))
            db_user = db_user_result.scalar_one_or_none()

            if tenant:
                # Get drafts owned by tenant
                drafts_result = await session.execute(
                    select(SpecDraft).where(SpecDraft.tenant_id == tenant.id)
                )
                user_drafts = list(drafts_result.scalars().all())

                # Get published specs for tenant
                specs_result = await session.execute(
                    select(Spec).where(
                        Spec.tenant_id == tenant.id,
                        Spec.status == SpecStatus.PUBLISHED,
                        Spec.deleted_at.is_(None),
                    )
                )
                published_specs = list(specs_result.scalars().all())

            # Also include drafts shared with user via SpecDraftMember
            if db_user:
                shared_result = await session.execute(
                    select(SpecDraft)
                    .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
                    .where(SpecDraftMember.user_id == db_user.id)
                )
                shared_drafts = list(shared_result.scalars().all())
                # Add shared drafts that aren't already in user_drafts
                existing_ids = {d.id for d in user_drafts}
                for draft in shared_drafts:
                    if draft.id not in existing_ids:
                        user_drafts.append(draft)

            # Add user drafts to profiles
            for draft in user_drafts:
                draft_key = f"draft:{draft.id}"
                profiles.append(draft_key)
                profile_versions[draft_key] = [draft.version]
                # Use display_name from spec_data if available
                display_name = draft.name
                if draft.spec_data:
                    spec_data = _extract_spec_data(draft.spec_data)
                    display_name = (
                        spec_data.get("display_name") or spec_data.get("name") or draft.name
                    )
                profile_display_names[draft_key] = f"{display_name} (Draft)"

            # Add published specs to profiles
            for spec in published_specs:
                spec_key = f"spec:{spec.id}"
                if spec_key not in profiles:
                    profiles.append(spec_key)
                    profile_versions[spec_key] = [spec.version]
                    # Use display_name from spec_data if available
                    display_name = spec.name
                    if spec.spec_data:
                        spec_data = _extract_spec_data(spec.spec_data)
                        display_name = (
                            spec_data.get("display_name") or spec_data.get("name") or spec.name
                        )
                    profile_display_names[spec_key] = f"{display_name} (Published)"

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
        from metaseed_hub.ui.dependencies import (
            get_current_user_from_cookie,
            get_tenant_for_user,
        )

        user = await get_current_user_from_cookie(request)
        if not user:
            return JSONResponse({"error": "Login required"}, status_code=401)

        form = await request.form()
        profile_specs = form.getlist("profiles")

        if len(profile_specs) < 1:
            return JSONResponse({"error": "Select at least 1 profile"}, status_code=400)

        tenant = await get_tenant_for_user(session, user)
        db_specs, profile_tuples = await load_profiles_for_comparison(
            session, [str(s) for s in profile_specs], tenant.id if tenant else None
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
        from metaseed_hub.ui.dependencies import (
            get_current_user_from_cookie,
            get_tenant_for_user,
        )

        user = await get_current_user_from_cookie(request)
        if not user:
            return JSONResponse({"error": "Login required"}, status_code=401)

        profile_specs = profiles.split(",")

        if len(profile_specs) < 1:
            return JSONResponse({"error": "At least 1 profile required"}, status_code=400)

        tenant = await get_tenant_for_user(session, user)
        db_specs, profile_tuples = await load_profiles_for_comparison(
            session, profile_specs, tenant.id if tenant else None
        )

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
