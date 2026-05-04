"""Spec Builder routes for Metaseed Hub.

Provides FastAPI routes for creating and editing ProfileSpec specifications
through an interactive web interface. Supports multiple specs per user with
workspace-based access control for team collaboration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import (
    Constraints,
    EntityDefSpec,
    FieldSpec,
    FieldType,
    ProfileSpec,
    ValidationRuleSpec,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from starlette.responses import Response

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecStatus,
    Team,
    TeamMembership,
    TeamRole,
    Tenant,
    User,
    Workspace,
    WorkspaceTeam,
)

from .spec_builder_helpers import (
    clone_spec,
    create_empty_spec,
    list_available_templates,
    spec_to_yaml,
    validate_entity_name,
    validate_field_name,
)
from .spec_builder_state import SpecBuilderState

logger = logging.getLogger(__name__)

# LRU cache with bounded size to prevent unbounded memory growth
_STATE_CACHE_MAX_SIZE = 100


class _StateCache:
    """Bounded LRU cache for draft states.

    Uses an OrderedDict to maintain LRU ordering and enforce a maximum size.
    Thread-safety note: This is sufficient for single-process deployments.
    For multi-process deployments, consider using Redis or similar.
    """

    def __init__(self, max_size: int = _STATE_CACHE_MAX_SIZE) -> None:
        self._cache: dict[str, SpecBuilderState] = {}
        self._max_size = max_size

    def get(self, key: str) -> SpecBuilderState | None:
        """Get item from cache, moving it to end (most recently used)."""
        if key in self._cache:
            # Move to end (most recently used)
            value = self._cache.pop(key)
            self._cache[key] = value
            return value
        return None

    def set(self, key: str, value: SpecBuilderState) -> None:
        """Set item in cache, evicting oldest if at capacity."""
        if key in self._cache:
            # Update existing - move to end
            del self._cache[key]
        elif len(self._cache) >= self._max_size:
            # Evict oldest (first item)
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            logger.debug("Evicted draft %s from state cache", oldest_key)
        self._cache[key] = value

    def pop(self, key: str, default: SpecBuilderState | None = None) -> SpecBuilderState | None:
        """Remove and return item from cache."""
        return self._cache.pop(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._cache


_state_cache = _StateCache()


class LoginRequiredRedirectError(Exception):
    """Raised when user needs to login and should be redirected."""

    pass


async def get_user_context(
    request: Request,
    session: AsyncSession,
    *,
    redirect_on_unauthorized: bool = False,
) -> tuple[str, str]:
    """Get user_id and tenant_id from request context.

    Args:
        request: The FastAPI request
        session: Database session
        redirect_on_unauthorized: If True, raise LoginRequiredRedirectError instead of HTTPException

    Raises:
        HTTPException 401 if user is not authenticated (API routes)
        LoginRequiredRedirectError if redirect_on_unauthorized=True (page routes)
    """
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    token_user = await get_current_user_from_cookie(request)

    if not token_user:
        if redirect_on_unauthorized:
            raise LoginRequiredRedirectError()
        raise HTTPException(status_code=401, detail="Login required")

    # Get or create tenant for this user (using keycloak_id prefix as slug)
    slug = token_user.keycloak_id[:8]
    tenant_result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    tenant: Tenant | None = tenant_result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name=token_user.name or token_user.email, slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
    assert tenant is not None  # Guaranteed by create above

    # Look up or create the User record
    user_result = await session.execute(
        select(User).where(
            User.keycloak_id == token_user.keycloak_id,
            User.tenant_id == tenant.id,
        )
    )
    user: User | None = user_result.scalar_one_or_none()
    if not user:
        user = User(
            keycloak_id=token_user.keycloak_id,
            tenant_id=tenant.id,
            email=token_user.email,
            display_name=token_user.name or token_user.email,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    assert user is not None  # Guaranteed by create above

    return user.id, tenant.id


# -----------------------------------------------------------------------------
# Access Control Helpers
# -----------------------------------------------------------------------------


async def get_user_workspaces(
    session: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> list[Workspace]:
    """Get workspaces accessible to user through team membership.

    A user can access a workspace if:
    - They are a member of a team that has access to the workspace
    - OR the workspace belongs to their tenant (temporary fallback for migration)

    Args:
        session: Database session
        user_id: User ID
        tenant_id: Tenant ID

    Returns:
        List of accessible workspaces
    """
    # Get workspaces through team membership
    result = await session.execute(
        select(Workspace)
        .join(WorkspaceTeam, Workspace.id == WorkspaceTeam.workspace_id)
        .join(Team, WorkspaceTeam.team_id == Team.id)
        .join(TeamMembership, Team.id == TeamMembership.team_id)
        .where(
            TeamMembership.user_id == user_id,
            Workspace.deleted_at.is_(None),
        )
        .distinct()
    )
    team_workspaces = list(result.scalars().all())

    if team_workspaces:
        return team_workspaces

    # Fallback: return all workspaces in the tenant (for migration/backwards compat)
    # TODO: Remove this fallback after migrating existing users to team memberships
    logger.warning(
        "User %s has no team memberships, falling back to tenant-wide workspace access. "
        "This fallback will be removed in a future version.",
        user_id,
    )
    result = await session.execute(
        select(Workspace).where(
            Workspace.tenant_id == tenant_id,
            Workspace.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def can_access_workspace(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
) -> bool:
    """Check if user has access to workspace via team membership.

    Args:
        session: Database session
        user_id: User ID
        workspace_id: Workspace ID

    Returns:
        True if user can access the workspace
    """
    # Check team-based access
    result = await session.execute(
        select(WorkspaceTeam)
        .join(Team, WorkspaceTeam.team_id == Team.id)
        .join(TeamMembership, Team.id == TeamMembership.team_id)
        .where(
            WorkspaceTeam.workspace_id == workspace_id,
            TeamMembership.user_id == user_id,
        )
    )
    if result.scalar_one_or_none():
        return True

    # Fallback: check if workspace is in user's tenant (backwards compat)
    # TODO: Remove this fallback after migrating existing users to team memberships
    result = await session.execute(
        select(Workspace)
        .join(User, Workspace.tenant_id == User.tenant_id)
        .where(
            Workspace.id == workspace_id,
            User.id == user_id,
            Workspace.deleted_at.is_(None),
        )
    )
    has_fallback_access = result.scalar_one_or_none() is not None
    if has_fallback_access:
        logger.warning(
            "User %s accessing workspace %s via tenant fallback. "
            "This fallback will be removed in a future version.",
            user_id,
            workspace_id,
        )
    return has_fallback_access


async def can_edit_spec(
    session: AsyncSession,
    user_id: str,
    spec_id: str,
) -> bool:
    """Check if user can edit spec (workspace member with ADMIN/OWNER role or author).

    Args:
        session: Database session
        user_id: User ID
        spec_id: Spec ID

    Returns:
        True if user can edit the spec
    """
    # Get the spec with workspace info
    result = await session.execute(
        select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
    )
    spec = result.scalar_one_or_none()
    if not spec:
        return False

    # Author can always edit
    if spec.created_by_id == user_id:
        return True

    # Check if user has ADMIN/OWNER role in a team with access to the workspace
    result = await session.execute(
        select(TeamMembership)
        .join(Team, TeamMembership.team_id == Team.id)
        .join(WorkspaceTeam, Team.id == WorkspaceTeam.team_id)
        .where(
            WorkspaceTeam.workspace_id == spec.workspace_id,
            TeamMembership.user_id == user_id,
            TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
        )
    )
    return result.scalar_one_or_none() is not None


async def get_or_create_default_workspace(
    session: AsyncSession,
    tenant_id: str,
) -> Workspace:
    """Get or create a default workspace for a tenant.

    Used when a tenant has no workspaces and we need one for the user.

    Args:
        session: Database session
        tenant_id: Tenant ID

    Returns:
        The default workspace
    """
    result = await session.execute(
        select(Workspace)
        .where(
            Workspace.tenant_id == tenant_id,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at)
    )
    workspace = result.scalars().first()

    if workspace:
        return workspace

    # Create a default workspace
    workspace = Workspace(
        tenant_id=tenant_id,
        name="Default",
        description="Default workspace",
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def load_state_for_draft(
    session: AsyncSession,
    draft_id: str,
    user_id: str,
) -> tuple[SpecBuilderState, SpecDraft]:
    """Load state from database for a specific draft.

    Args:
        session: Database session
        draft_id: Draft ID to load
        user_id: User ID (for access control)

    Returns:
        Tuple of (SpecBuilderState, SpecDraft)

    Raises:
        HTTPException 404 if draft not found or not accessible
    """
    # Check cache first
    cached_state = _state_cache.get(draft_id)
    if cached_state is not None:
        result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.workspace))
            .where(SpecDraft.id == draft_id)
        )
        draft = result.scalar_one_or_none()
        if draft and draft.user_id == user_id:
            return cached_state, draft

    # Load from database
    result = await session.execute(
        select(SpecDraft).options(selectinload(SpecDraft.workspace)).where(SpecDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this draft")

    if draft.spec_data:
        state = SpecBuilderState.from_dict(draft.spec_data)
    else:
        state = SpecBuilderState()

    _state_cache.set(draft_id, state)
    return state, draft


async def save_state_to_draft(
    session: AsyncSession,
    state: SpecBuilderState,
    draft: SpecDraft,
) -> None:
    """Save state to an existing draft.

    Args:
        session: Database session
        state: Builder state to save
        draft: Draft to update
    """
    if state.spec is None:
        # Delete draft if spec is cleared
        await session.delete(draft)
        await session.commit()
        _state_cache.pop(draft.id, None)
        return

    spec_data = state.to_dict()

    draft.name = state.spec.name or draft.name
    draft.version = state.spec.version
    draft.spec_data = spec_data
    flag_modified(draft, "spec_data")
    if state.template_source:
        draft.template_source = f"{state.template_source[0]}:{state.template_source[1]}"

    await session.commit()
    _state_cache.set(draft.id, state)


@dataclass
class DraftContext:
    """Context for draft-specific route handlers.

    Encapsulates the common pattern of loading user context, draft, and
    builder state that's needed by most draft manipulation endpoints.

    Attributes:
        builder: The SpecBuilderState for editing
        draft: The SpecDraft database record
        user_id: The authenticated user's ID
        tenant_id: The user's tenant ID
        spec: Shortcut to builder.spec (guaranteed non-None)
    """

    builder: SpecBuilderState
    draft: SpecDraft
    user_id: str
    tenant_id: str

    @property
    def spec(self) -> ProfileSpec:
        """Get the spec, guaranteed to be non-None."""
        assert self.builder.spec is not None
        return self.builder.spec

    async def save(self, session: AsyncSession) -> None:
        """Save the current state to the draft."""
        await save_state_to_draft(session, self.builder, self.draft)


async def get_draft_context(
    request: Request,
    draft_id: Annotated[str, Path()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftContext:
    """FastAPI dependency that loads and validates draft context.

    Use this dependency in route handlers that manipulate a specific draft.
    It handles authentication, loads the draft and builder state, and
    validates that a spec exists.

    Args:
        request: The FastAPI request
        draft_id: Draft ID from path parameter
        session: Database session

    Returns:
        DraftContext with validated builder, draft, and user info

    Raises:
        HTTPException 401: If user is not authenticated
        HTTPException 403: If user doesn't own the draft
        HTTPException 404: If draft not found
        HTTPException 400: If no spec in progress
    """
    user_id, tenant_id = await get_user_context(request, session)
    builder, draft = await load_state_for_draft(session, draft_id, user_id)

    if builder.spec is None:
        raise HTTPException(status_code=400, detail="No spec in progress")

    return DraftContext(
        builder=builder,
        draft=draft,
        user_id=user_id,
        tenant_id=tenant_id,
    )


# Type alias for the dependency
DraftContextDep = Annotated[DraftContext, Depends(get_draft_context)]


async def create_new_draft(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
    name: str,
    spec: ProfileSpec,
    template_source: tuple[str, str] | None = None,
    source_spec_id: str | None = None,
) -> SpecDraft:
    """Create a new draft in a workspace.

    Args:
        session: Database session
        user_id: User ID
        workspace_id: Workspace ID
        name: Draft name
        spec: ProfileSpec object
        template_source: Optional template source tuple
        source_spec_id: Optional source spec ID (for editing published specs)

    Returns:
        The created SpecDraft
    """
    state = SpecBuilderState()
    state.spec = spec
    state.template_source = template_source

    draft = SpecDraft(
        user_id=user_id,
        workspace_id=workspace_id,
        source_spec_id=source_spec_id,
        name=name,
        version=spec.version,
        spec_data=state.to_dict(),
        template_source=(f"{template_source[0]}:{template_source[1]}" if template_source else None),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)

    _state_cache.set(draft.id, state)
    return draft


def create_spec_builder_router(
    templates: Jinja2Templates,
) -> APIRouter:
    """Create the spec builder router with routes.

    Args:
        templates: Jinja2Templates instance.

    Returns:
        Configured APIRouter.
    """
    from metaseed_hub.ui.app import get_version_info

    router = APIRouter(prefix="/spec-builder", tags=["spec-builder"])

    def render(request: Request, template: str, context: dict[str, Any]) -> Response:
        """Render template with version info included."""
        context["version_info"] = get_version_info()
        return templates.TemplateResponse(request, template, context)

    # -------------------------------------------------------------------------
    # List page and workspace selection
    # -------------------------------------------------------------------------

    @router.get("", response_model=None)
    async def spec_builder_list(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Render the specs list page showing drafts and published specs."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Get accessible workspaces
        workspaces = await get_user_workspaces(session, user_id, tenant_id)

        if not workspaces:
            # Create a default workspace if none exist
            workspace = await get_or_create_default_workspace(session, tenant_id)
            workspaces = [workspace]

        workspace_ids = [w.id for w in workspaces]

        # Get user's drafts in accessible workspaces
        result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.workspace))
            .where(
                SpecDraft.user_id == user_id,
                SpecDraft.workspace_id.in_(workspace_ids),
            )
            .order_by(SpecDraft.updated_at.desc())
        )
        drafts = list(result.scalars().all())

        # Get published specs in accessible workspaces
        result = await session.execute(
            select(Spec)
            .options(selectinload(Spec.workspace), selectinload(Spec.created_by))
            .where(
                Spec.workspace_id.in_(workspace_ids),
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
            .order_by(Spec.updated_at.desc())
        )
        specs = list(result.scalars().all())

        return render(
            request,
            "spec_builder/list.html",
            {
                "drafts": drafts,
                "specs": specs,
                "workspaces": workspaces,
            },
        )

    # -------------------------------------------------------------------------
    # Create new draft
    # -------------------------------------------------------------------------

    @router.get("/new", response_model=None)
    async def new_spec_form(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: str | None = None,
    ) -> Response:
        """Show form to create a new spec draft."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        workspaces = await get_user_workspaces(session, user_id, tenant_id)

        if not workspaces:
            workspace = await get_or_create_default_workspace(session, tenant_id)
            workspaces = [workspace]

        available_templates = list_available_templates()

        return render(
            request,
            "spec_builder/new.html",
            {
                "workspaces": workspaces,
                "selected_workspace_id": workspace_id or (workspaces[0].id if workspaces else None),
                "templates": available_templates,
            },
        )

    @router.post("/new", response_model=None)
    async def create_new_spec(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: str = Form(...),
        name: str = Form(""),
        template: str = Form(""),
    ) -> Response:
        """Create a new spec draft."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Verify workspace access
        if not await can_access_workspace(session, user_id, workspace_id):
            raise HTTPException(status_code=403, detail="Access denied to workspace")

        # Create spec from template or empty
        template_source = None
        if template and ":" in template:
            profile, version = template.split(":", 1)
            try:
                spec = clone_spec(profile, version)
                template_source = (profile, version)
            except ValueError:
                spec = create_empty_spec()
        else:
            spec = create_empty_spec()

        # Set name if provided
        if name.strip():
            spec.name = name.strip()

        draft_name = spec.name if hasattr(spec, "name") and spec.name else "Untitled"

        draft = await create_new_draft(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            name=draft_name,
            spec=spec,
            template_source=template_source,
        )

        return RedirectResponse(
            url=f"/hub/spec-builder/{draft.id}",
            status_code=302,
        )

    @router.get("/clone/{profile}/{version}", response_model=None)
    async def clone_template(
        request: Request,
        profile: str,
        version: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: str | None = None,
    ) -> Response:
        """Clone an existing spec as a template."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        try:
            spec = clone_spec(profile, version)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        # Get workspace - use provided or default to first accessible
        if workspace_id:
            if not await can_access_workspace(session, user_id, workspace_id):
                raise HTTPException(status_code=403, detail="Access denied to workspace")
        else:
            workspaces = await get_user_workspaces(session, user_id, tenant_id)
            if not workspaces:
                workspace = await get_or_create_default_workspace(session, tenant_id)
                workspace_id = workspace.id
            else:
                workspace_id = workspaces[0].id

        draft = await create_new_draft(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            name=spec.name,
            spec=spec,
            template_source=(profile, version),
        )

        return RedirectResponse(
            url=f"/hub/spec-builder/{draft.id}",
            status_code=302,
        )

    # -------------------------------------------------------------------------
    # Draft editor
    # -------------------------------------------------------------------------

    @router.get("/{draft_id}", response_model=None)
    async def edit_draft(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Edit a specific draft."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        builder, draft = await load_state_for_draft(session, draft_id, user_id)

        if builder.spec is None:
            builder.spec = create_empty_spec()
            await save_state_to_draft(session, builder, draft)

        return render(
            request,
            "spec_builder/base.html",
            {
                "draft": draft,
                "draft_id": draft_id,
                "workspace": draft.workspace,
                "spec": builder.spec,
                "editing_entity": builder.editing_entity,
                "has_unsaved_changes": builder.has_unsaved_changes,
                "template_source": builder.template_source,
                "field_types": [t.value for t in FieldType],
            },
        )

    @router.delete("/{draft_id}", response_model=None)
    async def delete_draft(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Delete a draft."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = result.scalar_one_or_none()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        if draft.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        await session.delete(draft)
        await session.commit()
        _state_cache.pop(draft_id, None)

        return HTMLResponse(
            content='<div hx-redirect="/hub/spec-builder"></div>',
            headers={"HX-Redirect": "/hub/spec-builder"},
        )

    # -------------------------------------------------------------------------
    # Publishing
    # -------------------------------------------------------------------------

    @router.post("/{draft_id}/publish", response_class=HTMLResponse)
    async def publish_draft(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Publish a draft as a spec."""
        user_id, tenant_id = await get_user_context(request, session)

        builder, draft = await load_state_for_draft(session, draft_id, user_id)

        if builder.spec is None:
            raise HTTPException(status_code=400, detail="No spec to publish")

        if not builder.spec.name:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/save_result.html",
                {"error": "Profile name is required before publishing"},
            )

        # Check if updating existing spec or creating new
        if draft.source_spec_id:
            # Update existing spec version
            result = await session.execute(select(Spec).where(Spec.id == draft.source_spec_id))
            existing_spec = result.scalar_one_or_none()
            if existing_spec and not await can_edit_spec(session, user_id, existing_spec.id):
                raise HTTPException(status_code=403, detail="Cannot edit this spec")

        # Create new spec
        spec = Spec(
            workspace_id=draft.workspace_id,
            name=builder.spec.name,
            version=builder.spec.version,
            description=builder.spec.description,
            spec_data=builder.to_dict(),
            status=SpecStatus.PUBLISHED,
            created_by_id=user_id,
        )
        session.add(spec)

        # Delete the draft after publishing
        await session.delete(draft)
        await session.commit()
        _state_cache.pop(draft_id, None)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/save_result.html",
            {
                "success": True,
                "message": "Specification published successfully",
                "redirect_url": "/hub/spec-builder",
            },
        )

    @router.get("/{draft_id}/reset")
    async def reset_draft(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> RedirectResponse:
        """Reset a draft to empty state."""
        user_id, _tenant_id = await get_user_context(request, session)

        builder, draft = await load_state_for_draft(session, draft_id, user_id)

        # Create fresh empty spec
        builder.spec = ProfileSpec(
            name=draft.name,
            version=draft.version,
            root_entity="",
            entities={},
            validation_rules=[],
        )

        # Save to database
        draft.spec_data = builder.to_dict()
        await session.commit()

        # Clear cache
        _state_cache.pop(draft_id, None)

        return RedirectResponse(
            url=f"/hub/spec-builder/{draft_id}",
            status_code=303,
        )

    # -------------------------------------------------------------------------
    # View and edit published specs
    # -------------------------------------------------------------------------

    @router.get("/spec/{spec_id}", response_model=None)
    async def view_spec(
        request: Request,
        spec_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """View a published spec (read-only)."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        result = await session.execute(
            select(Spec)
            .options(selectinload(Spec.workspace), selectinload(Spec.created_by))
            .where(Spec.id == spec_id, Spec.deleted_at.is_(None))
        )
        spec = result.scalar_one_or_none()

        if not spec:
            raise HTTPException(status_code=404, detail="Spec not found")

        if not await can_access_workspace(session, user_id, spec.workspace_id):
            raise HTTPException(status_code=403, detail="Access denied")

        # Load spec data into builder state for display
        builder = (
            SpecBuilderState.from_dict(spec.spec_data) if spec.spec_data else SpecBuilderState()
        )

        return render(
            request,
            "spec_builder/view.html",
            {
                "spec_record": spec,
                "spec": builder.spec,
                "workspace": spec.workspace,
                "can_edit": await can_edit_spec(session, user_id, spec_id),
            },
        )

    @router.post("/spec/{spec_id}/edit", response_model=None)
    async def create_draft_from_spec(
        request: Request,
        spec_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Create a draft from a published spec for editing."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        result = await session.execute(
            select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
        )
        spec = result.scalar_one_or_none()

        if not spec:
            raise HTTPException(status_code=404, detail="Spec not found")

        if not await can_access_workspace(session, user_id, spec.workspace_id):
            raise HTTPException(status_code=403, detail="Access denied")

        # Load spec data
        builder = (
            SpecBuilderState.from_dict(spec.spec_data) if spec.spec_data else SpecBuilderState()
        )

        if builder.spec is None:
            raise HTTPException(status_code=400, detail="Invalid spec data")

        draft = await create_new_draft(
            session,
            user_id=user_id,
            workspace_id=spec.workspace_id,
            name=spec.name,
            spec=builder.spec,
            source_spec_id=spec.id,
        )

        return RedirectResponse(
            url=f"/hub/spec-builder/{draft.id}",
            status_code=302,
        )

    # -------------------------------------------------------------------------
    # Profile metadata (draft-specific routes)
    # -------------------------------------------------------------------------

    @router.get("/{draft_id}/profile-metadata", response_class=HTMLResponse)
    async def get_profile_metadata_form(
        request: Request,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get the profile metadata form."""
        return templates.TemplateResponse(
            request,
            "spec_builder/partials/profile_metadata_form.html",
            {"spec": ctx.spec, "draft_id": ctx.draft.id},
        )

    @router.post("/{draft_id}/profile-metadata", response_class=HTMLResponse)
    async def update_profile_metadata(
        request: Request,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(""),
        version: str = Form("1.0"),
        display_name: str = Form(""),
        description: str = Form(""),
        ontology: str = Form(""),
        root_entity: str = Form(""),
    ) -> HTMLResponse:
        """Update profile metadata."""
        ctx.spec.name = name.strip()
        ctx.spec.version = version.strip() or "1.0"
        ctx.spec.display_name = display_name.strip() or None
        ctx.spec.description = description.strip()
        ctx.spec.ontology = ontology.strip() or None
        ctx.spec.root_entity = root_entity.strip()
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/profile_metadata_form.html",
            {"spec": ctx.spec, "draft_id": ctx.draft.id, "success": True},
        )

    # -------------------------------------------------------------------------
    # Entities management (draft-specific routes)
    # -------------------------------------------------------------------------

    @router.get("/{draft_id}/entities", response_class=HTMLResponse)
    async def get_entities_list(
        request: Request,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get the entities list panel."""
        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entities_list.html",
            {
                "draft_id": ctx.draft.id,
                "entities": ctx.spec.entities,
                "editing_entity": ctx.builder.editing_entity,
                "root_entity": ctx.spec.root_entity,
            },
        )

    @router.post("/{draft_id}/entity", response_class=HTMLResponse)
    async def add_entity(
        request: Request,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(...),
    ) -> HTMLResponse:
        """Add a new entity."""
        name = name.strip()
        error = validate_entity_name(name)
        if error:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/entities_list.html",
                {
                    "draft_id": ctx.draft.id,
                    "entities": ctx.spec.entities,
                    "editing_entity": ctx.builder.editing_entity,
                    "root_entity": ctx.spec.root_entity,
                    "error": error,
                },
            )

        if name in ctx.spec.entities:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/entities_list.html",
                {
                    "draft_id": ctx.draft.id,
                    "entities": ctx.spec.entities,
                    "editing_entity": ctx.builder.editing_entity,
                    "root_entity": ctx.spec.root_entity,
                    "error": f"Entity '{name}' already exists",
                },
            )

        ctx.spec.entities[name] = EntityDefSpec(
            ontology_term=None,
            description="",
            fields=[],
        )
        ctx.builder.editing_entity = name
        ctx.builder.mark_changed()

        # If this is the first entity and no root is set, make it the root
        if not ctx.spec.root_entity:
            ctx.spec.root_entity = name

        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": name,
                "entity": ctx.spec.entities[name],
                "editing_field_idx": None,
                "field_types": [t.value for t in FieldType],
            },
        )

    @router.get("/{draft_id}/entity/{name}", response_class=HTMLResponse)
    async def get_entity(
        request: Request,
        name: str,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get entity editor form."""
        if name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")

        ctx.builder.editing_entity = name
        ctx.builder.editing_field_idx = None

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": name,
                "entity": ctx.spec.entities[name],
                "editing_field_idx": None,
                "field_types": [t.value for t in FieldType],
            },
        )

    @router.put("/{draft_id}/entity/{name}", response_class=HTMLResponse)
    async def update_entity(
        request: Request,
        name: str,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        new_name: str = Form(""),
        description: str = Form(""),
        ontology_term: str = Form(""),
    ) -> HTMLResponse:
        """Update entity metadata including rename."""
        if name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")

        entity = ctx.spec.entities[name]
        entity.description = description.strip()
        entity.ontology_term = ontology_term.strip() or None

        # Handle rename
        new_name = new_name.strip()
        final_name = name
        if new_name and new_name != name:
            # Validate new name
            error = validate_entity_name(new_name)
            if error:
                return templates.TemplateResponse(
                    request,
                    "spec_builder/partials/entity_editor.html",
                    {
                        "draft_id": ctx.draft.id,
                        "spec": ctx.spec,
                        "entity_name": name,
                        "entity": entity,
                        "editing_field_idx": None,
                        "field_types": [t.value for t in FieldType],
                        "error": error,
                    },
                )
            if new_name in ctx.spec.entities:
                return templates.TemplateResponse(
                    request,
                    "spec_builder/partials/entity_editor.html",
                    {
                        "draft_id": ctx.draft.id,
                        "spec": ctx.spec,
                        "entity_name": name,
                        "entity": entity,
                        "editing_field_idx": None,
                        "field_types": [t.value for t in FieldType],
                        "error": f"Entity '{new_name}' already exists",
                    },
                )

            # Rename: add with new name, remove old
            ctx.spec.entities[new_name] = entity
            del ctx.spec.entities[name]
            final_name = new_name

            # Update root_entity if it was this entity
            if ctx.spec.root_entity == name:
                ctx.spec.root_entity = new_name

            # Update editing_entity if it was this entity
            if ctx.builder.editing_entity == name:
                ctx.builder.editing_entity = new_name

            # Update references in all other entities' fields
            for other_entity in ctx.spec.entities.values():
                for field in other_entity.fields:
                    # Update items (relationship target)
                    if field.items == name:
                        field.items = new_name
                    # Update reference (e.g., "OldName.field" -> "NewName.field")
                    if field.reference and field.reference.startswith(f"{name}."):
                        field.reference = f"{new_name}.{field.reference[len(name)+1:]}"
                    # Update parent_ref
                    if field.parent_ref and field.parent_ref.startswith(f"{name}."):
                        field.parent_ref = f"{new_name}.{field.parent_ref[len(name)+1:]}"

            # Update validation rules that reference this entity
            for rule in ctx.spec.validation_rules:
                if rule.applies_to == name:
                    rule.applies_to = new_name
                elif isinstance(rule.applies_to, list) and name in rule.applies_to:
                    rule.applies_to = [new_name if e == name else e for e in rule.applies_to]

        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": final_name,
                "entity": entity,
                "editing_field_idx": None,
                "field_types": [t.value for t in FieldType],
                "success": True,
            },
        )

    @router.delete("/{draft_id}/entity/{name}", response_class=HTMLResponse)
    async def delete_entity(
        request: Request,
        name: str,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete an entity."""
        if name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")

        del ctx.spec.entities[name]

        # Clear editing state if we were editing this entity
        if ctx.builder.editing_entity == name:
            ctx.builder.editing_entity = None
            ctx.builder.editing_field_idx = None

        # Clear root_entity if it was this entity
        if ctx.spec.root_entity == name:
            ctx.spec.root_entity = ""

        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entities_list.html",
            {
                "draft_id": ctx.draft.id,
                "entities": ctx.spec.entities,
                "editing_entity": ctx.builder.editing_entity,
                "root_entity": ctx.spec.root_entity,
            },
        )

    # -------------------------------------------------------------------------
    # Fields management (draft-specific routes)
    # -------------------------------------------------------------------------

    @router.post("/{draft_id}/entity/{entity_name}/field", response_class=HTMLResponse)
    async def add_field(
        request: Request,
        entity_name: str,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(...),
        field_type: str = Form("string"),
    ) -> HTMLResponse:
        """Add a new field to an entity."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        name = name.strip()
        error = validate_field_name(name)
        if error:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/entity_editor.html",
                {
                    "draft_id": ctx.draft.id,
                    "spec": ctx.spec,
                    "entity_name": entity_name,
                    "entity": ctx.spec.entities[entity_name],
                    "editing_field_idx": None,
                    "field_types": [t.value for t in FieldType],
                    "error": error,
                },
            )

        entity = ctx.spec.entities[entity_name]

        # Check for duplicate field name
        for f in entity.fields:
            if f.name == name:
                return templates.TemplateResponse(
                    request,
                    "spec_builder/partials/entity_editor.html",
                    {
                        "draft_id": ctx.draft.id,
                        "spec": ctx.spec,
                        "entity_name": entity_name,
                        "entity": entity,
                        "editing_field_idx": None,
                        "field_types": [t.value for t in FieldType],
                        "error": f"Field '{name}' already exists",
                    },
                )

        new_field = FieldSpec(
            name=name,
            type=FieldType(field_type),
            required=False,
            description="",
        )
        entity.fields.append(new_field)
        ctx.builder.editing_field_idx = len(entity.fields) - 1
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": entity_name,
                "entity": entity,
                "editing_field_idx": ctx.builder.editing_field_idx,
                "field_types": [t.value for t in FieldType],
            },
        )

    @router.get("/{draft_id}/entity/{entity_name}/field/{idx}", response_class=HTMLResponse)
    async def get_field_form(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get field editor form."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        entity = ctx.spec.entities[entity_name]
        if idx < 0 or idx >= len(entity.fields):
            raise HTTPException(status_code=404, detail="Field not found")

        ctx.builder.editing_field_idx = idx

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/field_form.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": entity_name,
                "field": entity.fields[idx],
                "field_idx": idx,
                "field_types": [t.value for t in FieldType],
            },
        )

    @router.put("/{draft_id}/entity/{entity_name}/field/{idx}", response_class=HTMLResponse)
    async def update_field(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(...),
        field_type: str = Form("string"),
        required: bool = Form(False),
        description: str = Form(""),
        ontology_term: str = Form(""),
        codename: str = Form(""),
        items: str = Form(""),
        parent_ref: str = Form(""),
        pattern: str = Form(""),
        min_length: str = Form(""),
        max_length: str = Form(""),
        minimum: str = Form(""),
        maximum: str = Form(""),
        min_items: str = Form(""),
        max_items: str = Form(""),
        enum_values: str = Form(""),
        unique_within: str = Form(""),
        reference: str = Form(""),
    ) -> HTMLResponse:
        """Update a field."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        entity = ctx.spec.entities[entity_name]
        if idx < 0 or idx >= len(entity.fields):
            raise HTTPException(status_code=404, detail="Field not found")

        # Build constraints if any are provided
        constraints = None
        has_constraints = any(
            [
                pattern,
                min_length,
                max_length,
                minimum,
                maximum,
                min_items,
                max_items,
                enum_values,
            ]
        )
        if has_constraints:
            constraints = Constraints(
                pattern=pattern.strip() or None,
                min_length=int(min_length) if min_length.strip() else None,
                max_length=int(max_length) if max_length.strip() else None,
                minimum=float(minimum) if minimum.strip() else None,
                maximum=float(maximum) if maximum.strip() else None,
                min_items=int(min_items) if min_items.strip() else None,
                max_items=int(max_items) if max_items.strip() else None,
                enum=[v.strip() for v in enum_values.split("\n") if v.strip()]
                if enum_values.strip()
                else None,
            )

        # Update field
        field = entity.fields[idx]
        field.name = name.strip()
        field.type = FieldType(field_type)
        field.required = required
        field.description = description.strip()
        field.ontology_term = ontology_term.strip() or None
        field.codename = codename.strip() or None
        field.items = items.strip() or None
        field.parent_ref = parent_ref.strip() or None
        field.unique_within = unique_within.strip() or None
        field.reference = reference.strip() or None
        field.constraints = constraints

        ctx.builder.editing_field_idx = None
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": entity_name,
                "entity": entity,
                "editing_field_idx": None,
                "field_types": [t.value for t in FieldType],
                "success": True,
            },
        )

    @router.delete("/{draft_id}/entity/{entity_name}/field/{idx}", response_class=HTMLResponse)
    async def delete_field(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete a field from an entity."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        entity = ctx.spec.entities[entity_name]
        if idx < 0 or idx >= len(entity.fields):
            raise HTTPException(status_code=404, detail="Field not found")

        del entity.fields[idx]
        ctx.builder.editing_field_idx = None
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/entity_editor.html",
            {
                "draft_id": ctx.draft.id,
                "spec": ctx.spec,
                "entity_name": entity_name,
                "entity": entity,
                "editing_field_idx": None,
                "field_types": [t.value for t in FieldType],
            },
        )

    # -------------------------------------------------------------------------
    # Validation rules management (draft-specific routes)
    # -------------------------------------------------------------------------

    @router.get("/{draft_id}/validation-rules", response_class=HTMLResponse)
    async def get_validation_rules(
        request: Request,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get validation rules list."""
        return templates.TemplateResponse(
            request,
            "spec_builder/partials/validation_rules_list.html",
            {
                "draft_id": ctx.draft.id,
                "rules": ctx.spec.validation_rules,
                "editing_rule_idx": ctx.builder.editing_rule_idx,
                "entities": list(ctx.spec.entities.keys()),
            },
        )

    @router.post("/{draft_id}/validation-rule", response_class=HTMLResponse)
    async def add_validation_rule(
        request: Request,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(...),
    ) -> HTMLResponse:
        """Add a new validation rule."""
        name = name.strip()
        if not name:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/validation_rules_list.html",
                {
                    "draft_id": ctx.draft.id,
                    "rules": ctx.spec.validation_rules,
                    "editing_rule_idx": None,
                    "entities": list(ctx.spec.entities.keys()),
                    "error": "Rule name is required",
                },
            )

        new_rule = ValidationRuleSpec(
            name=name,
            description="",
            applies_to="all",
        )
        ctx.spec.validation_rules.append(new_rule)
        ctx.builder.editing_rule_idx = len(ctx.spec.validation_rules) - 1
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/validation_rule_form.html",
            {
                "draft_id": ctx.draft.id,
                "rule": new_rule,
                "rule_idx": ctx.builder.editing_rule_idx,
                "entities": list(ctx.spec.entities.keys()),
            },
        )

    @router.get("/{draft_id}/validation-rule/{idx}", response_class=HTMLResponse)
    async def get_validation_rule_form(
        request: Request,
        idx: int,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get validation rule editor form."""
        if idx < 0 or idx >= len(ctx.spec.validation_rules):
            raise HTTPException(status_code=404, detail="Rule not found")

        ctx.builder.editing_rule_idx = idx

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/validation_rule_form.html",
            {
                "draft_id": ctx.draft.id,
                "rule": ctx.spec.validation_rules[idx],
                "rule_idx": idx,
                "entities": list(ctx.spec.entities.keys()),
            },
        )

    @router.put("/{draft_id}/validation-rule/{idx}", response_class=HTMLResponse)
    async def update_validation_rule(
        request: Request,
        idx: int,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: str = Form(...),
        description: str = Form(""),
        applies_to: str = Form("all"),
        field: str = Form(""),
        condition: str = Form(""),
        pattern: str = Form(""),
        minimum: str = Form(""),
        maximum: str = Form(""),
        enum_values: str = Form(""),
        reference: str = Form(""),
        unique_within: str = Form(""),
        min_items: str = Form(""),
        max_items: str = Form(""),
    ) -> HTMLResponse:
        """Update a validation rule."""
        if idx < 0 or idx >= len(ctx.spec.validation_rules):
            raise HTTPException(status_code=404, detail="Rule not found")

        rule = ctx.spec.validation_rules[idx]

        # Parse applies_to (can be "all" or comma-separated entity names)
        applies_to = applies_to.strip()
        if applies_to == "all":
            applies_to_value: str | list[str] = "all"
        else:
            applies_to_value = [e.strip() for e in applies_to.split(",") if e.strip()]
            if len(applies_to_value) == 1:
                applies_to_value = applies_to_value[0]

        rule.name = name.strip()
        rule.description = description.strip()
        rule.applies_to = applies_to_value
        rule.field = field.strip() or None
        rule.condition = condition.strip() or None
        rule.pattern = pattern.strip() or None
        rule.minimum = float(minimum) if minimum.strip() else None
        rule.maximum = float(maximum) if maximum.strip() else None
        rule.enum = (
            [v.strip() for v in enum_values.split("\n") if v.strip()]
            if enum_values.strip()
            else None
        )
        rule.reference = reference.strip() or None
        rule.unique_within = unique_within.strip() or None
        rule.min_items = int(min_items) if min_items.strip() else None
        rule.max_items = int(max_items) if max_items.strip() else None

        ctx.builder.editing_rule_idx = None
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/validation_rules_list.html",
            {
                "draft_id": ctx.draft.id,
                "rules": ctx.spec.validation_rules,
                "editing_rule_idx": None,
                "entities": list(ctx.spec.entities.keys()),
                "success": True,
            },
        )

    @router.delete("/{draft_id}/validation-rule/{idx}", response_class=HTMLResponse)
    async def delete_validation_rule(
        request: Request,
        idx: int,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete a validation rule."""
        if idx < 0 or idx >= len(ctx.spec.validation_rules):
            raise HTTPException(status_code=404, detail="Rule not found")

        del ctx.spec.validation_rules[idx]
        ctx.builder.editing_rule_idx = None
        ctx.builder.mark_changed()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/validation_rules_list.html",
            {
                "draft_id": ctx.draft.id,
                "rules": ctx.spec.validation_rules,
                "editing_rule_idx": None,
                "entities": list(ctx.spec.entities.keys()),
            },
        )

    # -------------------------------------------------------------------------
    # Preview and export (draft-specific routes)
    # -------------------------------------------------------------------------

    @router.get("/{draft_id}/preview", response_class=HTMLResponse)
    async def preview_yaml(
        request: Request,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Get YAML preview of the current spec."""
        yaml_content = spec_to_yaml(ctx.spec)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/yaml_preview.html",
            {"yaml_content": yaml_content, "draft_id": ctx.draft.id},
        )

    @router.get("/{draft_id}/export")
    async def export_yaml(
        request: Request,
        ctx: DraftContextDep,
    ) -> StreamingResponse:
        """Download the spec as a YAML file."""
        yaml_content = spec_to_yaml(ctx.spec)
        filename = f"{ctx.spec.name or 'profile'}.yaml"

        return StreamingResponse(
            iter([yaml_content]),
            media_type="application/x-yaml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/{draft_id}/save", response_class=HTMLResponse)
    async def save_spec_endpoint(
        request: Request,
        ctx: DraftContextDep,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Save the spec draft."""
        if not ctx.spec.name:
            return templates.TemplateResponse(
                request,
                "spec_builder/partials/save_result.html",
                {"error": "Profile name is required before saving"},
            )

        # Save to database and mark as saved
        ctx.builder.mark_saved()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/save_result.html",
            {"success": True, "message": "Draft saved successfully"},
        )

    return router
