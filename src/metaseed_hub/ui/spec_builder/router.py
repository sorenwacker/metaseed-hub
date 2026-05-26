"""Main router for spec builder.

Wires together all spec builder routes using the modular components.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import (
    EntityDefSpec,
    FieldSpec,
    FieldType,
    ProfileSpec,
    ValidationRuleSpec,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Dataset,
    ReactionType,
    Spec,
    SpecComment,
    SpecCommentReaction,
    SpecDraft,
    SpecDraftMember,
    SpecDraftRole,
    SpecStatus,
    User,
)

from ..spec_builder_helpers import (
    clone_spec,
    create_empty_spec,
    list_available_templates,
    parse_spec_from_yaml,
    spec_to_yaml,
    validate_entity_name,
    validate_field_name,
)
from .access import (
    DraftContext,
    LoginRequiredRedirectError,
    can_access_workspace,
    can_edit_spec,
    create_new_draft,
    get_draft_context,
    get_or_create_default_workspace,
    get_user_context,
    get_user_workspaces,
    load_state_for_draft,
    save_state_to_draft,
)
from .cache import state_cache
from .forms import FieldFormData, ValidationRuleFormData
from .state import SpecBuilderState

logger = logging.getLogger(__name__)

# Type alias for the draft context dependency
DraftContextDep = Annotated[DraftContext, Depends(get_draft_context)]


def create_spec_builder_router(templates: Jinja2Templates) -> APIRouter:
    """Create the spec builder router with all routes.

    Args:
        templates: Jinja2Templates instance.

    Returns:
        Configured APIRouter.
    """
    from metaseed_hub.ui.app import get_version_info

    router = APIRouter(prefix="/spec-builder", tags=["spec-builder"])

    async def render(request: Request, template: str, context: dict[str, Any]) -> Response:
        """Render template with version info, nav_active, and user included."""
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        context["version_info"] = get_version_info()
        context["nav_active"] = "spec-builder"
        # Add user to context for navbar
        if "user" not in context:
            context["user"] = await get_current_user_from_cookie(request)
        return templates.TemplateResponse(request, template, context)

    # =========================================================================
    # List and Create Routes
    # =========================================================================

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

        workspaces = await get_user_workspaces(session, user_id, tenant_id)
        if not workspaces:
            workspace = await get_or_create_default_workspace(session, tenant_id)
            workspaces = [workspace]

        workspace_ids = [w.id for w in workspaces]

        # Get user's database ID for membership check
        user_result = await session.execute(select(User).where(User.keycloak_id == user_id))
        db_user = user_result.scalar_one_or_none()
        db_user_id = db_user.id if db_user else None

        # Get drafts user owns
        owned_result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.workspace))
            .where(
                SpecDraft.user_id == user_id,
                SpecDraft.workspace_id.in_(workspace_ids),
            )
            .order_by(SpecDraft.updated_at.desc())
        )
        owned_drafts = list(owned_result.scalars().all())

        # Get drafts shared with user
        shared_drafts = []
        if db_user_id:
            shared_result = await session.execute(
                select(SpecDraft)
                .options(selectinload(SpecDraft.workspace))
                .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
                .where(SpecDraftMember.user_id == db_user_id)
                .order_by(SpecDraft.updated_at.desc())
            )
            shared_drafts = list(shared_result.scalars().all())

        # Combine and deduplicate
        seen_ids = set()
        drafts = []
        for draft in owned_drafts + shared_drafts:
            if draft.id not in seen_ids:
                seen_ids.add(draft.id)
                drafts.append(draft)

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

        return await render(
            request,
            "spec_builder/list.html",
            {"drafts": drafts, "specs": specs, "workspaces": workspaces},
        )

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

        return await render(
            request,
            "spec_builder/new.html",
            {
                "workspaces": workspaces,
                "selected_workspace_id": workspace_id or (workspaces[0].id if workspaces else None),
                "templates": list_available_templates(),
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

        if not await can_access_workspace(session, user_id, workspace_id):
            raise HTTPException(status_code=403, detail="Access denied to workspace")

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

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

    @router.get("/import", response_model=None)
    async def import_spec_form(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: str | None = None,
    ) -> Response:
        """Show form to import a spec from YAML file."""
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

        return await render(
            request,
            "spec_builder/import.html",
            {
                "workspaces": workspaces,
                "selected_workspace_id": workspace_id or (workspaces[0].id if workspaces else None),
            },
        )

    @router.post("/import", response_model=None)
    async def import_spec(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: str = Form(...),
        spec_file: UploadFile = File(...),
    ) -> Response:
        """Import a spec from uploaded YAML file."""
        try:
            user_id, tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        if not await can_access_workspace(session, user_id, workspace_id):
            raise HTTPException(status_code=403, detail="Access denied to workspace")

        if not spec_file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        if not spec_file.filename.endswith((".yaml", ".yml")):
            workspaces = await get_user_workspaces(session, user_id, tenant_id)
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "workspaces": workspaces,
                    "selected_workspace_id": workspace_id,
                    "error": "File must be a YAML file (.yaml or .yml)",
                },
            )

        try:
            content = await spec_file.read()
            yaml_content = content.decode("utf-8")
            spec = parse_spec_from_yaml(yaml_content)
        except UnicodeDecodeError:
            workspaces = await get_user_workspaces(session, user_id, tenant_id)
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "workspaces": workspaces,
                    "selected_workspace_id": workspace_id,
                    "error": "File must be UTF-8 encoded",
                },
            )
        except ValueError as e:
            workspaces = await get_user_workspaces(session, user_id, tenant_id)
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "workspaces": workspaces,
                    "selected_workspace_id": workspace_id,
                    "error": str(e),
                },
            )

        draft_name = spec.name if spec.name else "Imported Spec"

        draft = await create_new_draft(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            name=draft_name,
            spec=spec,
        )

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

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

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

    # =========================================================================
    # Draft Editor Routes
    # =========================================================================

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

        # Load draft owner
        owner_result = await session.execute(select(User).where(User.keycloak_id == draft.user_id))
        draft_owner = owner_result.scalar_one_or_none()

        # Load members for sharing tab
        members_result = await session.execute(
            select(SpecDraftMember)
            .where(SpecDraftMember.spec_draft_id == draft_id)
            .options(selectinload(SpecDraftMember.user))
        )
        members = list(members_result.scalars().all())

        # Get current user's database record
        current_user_result = await session.execute(select(User).where(User.keycloak_id == user_id))
        current_db_user = current_user_result.scalar_one_or_none()

        return await render(
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
                "members": members,
                "draft_owner": draft_owner,
                "current_user_id": current_db_user.id if current_db_user else None,
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

        # Check if any datasets are using this spec
        datasets_result = await session.execute(
            select(Dataset).where(Dataset.spec_draft_id == draft_id)
        )
        dependent_datasets = list(datasets_result.scalars().all())

        if dependent_datasets:
            dataset_names = ", ".join(d.name for d in dependent_datasets[:3])
            if len(dependent_datasets) > 3:
                dataset_names += f" and {len(dependent_datasets) - 3} more"
            msg = (
                f"Cannot delete: {len(dependent_datasets)} dataset(s) are using "
                f"this spec ({dataset_names}). Delete or migrate the datasets first."
            )
            return HTMLResponse(
                content=f'<div class="notification notification-error">{msg}</div>',
                headers={"HX-Reswap": "beforeend", "HX-Retarget": "#notification-container"},
            )

        await session.delete(draft)
        await session.commit()
        state_cache.pop(draft_id, None)

        return HTMLResponse(
            content='<div hx-redirect="/hub/spec-builder"></div>',
            headers={"HX-Redirect": "/hub/spec-builder"},
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

        builder.spec = ProfileSpec(
            name=draft.name,
            version=draft.version,
            root_entity="",
            entities={},
            validation_rules=[],
        )

        draft.spec_data = builder.to_dict()
        await session.commit()
        state_cache.pop(draft_id, None)

        return RedirectResponse(url=f"/hub/spec-builder/{draft_id}", status_code=303)

    # =========================================================================
    # Publishing Routes
    # =========================================================================

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

        if draft.source_spec_id:
            result = await session.execute(select(Spec).where(Spec.id == draft.source_spec_id))
            existing_spec = result.scalar_one_or_none()
            if existing_spec and not await can_edit_spec(session, user_id, existing_spec.id):
                raise HTTPException(status_code=403, detail="Cannot edit this spec")

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

        await session.delete(draft)
        await session.commit()
        state_cache.pop(draft_id, None)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/save_result.html",
            {
                "success": True,
                "message": "Specification published successfully",
                "redirect_url": "/hub/spec-builder",
            },
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

        ctx.builder.mark_saved()
        await ctx.save(session)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/save_result.html",
            {"success": True, "message": "Draft saved successfully"},
        )

    # =========================================================================
    # View Published Specs
    # =========================================================================

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

        builder = (
            SpecBuilderState.from_dict(spec.spec_data) if spec.spec_data else SpecBuilderState()
        )

        return await render(
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

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

    # =========================================================================
    # Profile Metadata Routes
    # =========================================================================

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
        version: str = Form("0.1"),
        display_name: str = Form(""),
        description: str = Form(""),
        ontology: str = Form(""),
        root_entity: str = Form(""),
    ) -> HTMLResponse:
        """Update profile metadata."""
        ctx.spec.name = name.strip()
        ctx.spec.version = version.strip() or "0.1"
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

    # =========================================================================
    # Entity Routes
    # =========================================================================

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

        new_name = new_name.strip()
        final_name = name
        if new_name and new_name != name:
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

            # Rename entity
            ctx.spec.entities[new_name] = entity
            del ctx.spec.entities[name]
            final_name = new_name

            if ctx.spec.root_entity == name:
                ctx.spec.root_entity = new_name
            if ctx.builder.editing_entity == name:
                ctx.builder.editing_entity = new_name

            # Update references in all entities
            for other_entity in ctx.spec.entities.values():
                for field in other_entity.fields:
                    if field.items == name:
                        field.items = new_name
                    if field.reference and field.reference.startswith(f"{name}."):
                        field.reference = f"{new_name}.{field.reference[len(name) + 1:]}"
                    if field.parent_ref and field.parent_ref.startswith(f"{name}."):
                        field.parent_ref = f"{new_name}.{field.parent_ref[len(name) + 1:]}"

            # Update validation rules
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

        if ctx.builder.editing_entity == name:
            ctx.builder.editing_entity = None
            ctx.builder.editing_field_idx = None

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

    # =========================================================================
    # Field Routes
    # =========================================================================

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

        # Use form data class for constraint building
        form_data = FieldFormData(
            name=name,
            field_type=field_type,
            required=required,
            description=description,
            ontology_term=ontology_term,
            codename=codename,
            items=items,
            parent_ref=parent_ref,
            pattern=pattern,
            min_length=min_length,
            max_length=max_length,
            minimum=minimum,
            maximum=maximum,
            min_items=min_items,
            max_items=max_items,
            enum_values=enum_values,
            unique_within=unique_within,
            reference=reference,
        )

        field = entity.fields[idx]
        field.name = form_data.name.strip()
        field.type = form_data.get_field_type()
        field.required = form_data.required
        field.description = form_data.description.strip()
        field.ontology_term = form_data.ontology_term.strip() or None
        field.codename = form_data.codename.strip() or None
        field.items = form_data.items.strip() or None
        field.parent_ref = form_data.parent_ref.strip() or None
        field.unique_within = form_data.unique_within.strip() or None
        field.reference = form_data.reference.strip() or None
        field.constraints = form_data.get_constraints()

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

    # =========================================================================
    # Validation Rules Routes
    # =========================================================================

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

        form_data = ValidationRuleFormData(
            name=name,
            description=description,
            applies_to=applies_to,
            field_name=field,
            condition=condition,
            pattern=pattern,
            minimum=minimum,
            maximum=maximum,
            enum_values=enum_values,
            reference=reference,
            unique_within=unique_within,
            min_items=min_items,
            max_items=max_items,
        )

        rule = ctx.spec.validation_rules[idx]
        rule.name = form_data.name.strip()
        rule.description = form_data.description.strip()
        rule.applies_to = form_data.get_applies_to()
        rule.field = form_data.field_name.strip() or None
        rule.condition = form_data.condition.strip() or None
        rule.pattern = form_data.pattern.strip() or None
        rule.minimum = float(form_data.minimum) if form_data.minimum.strip() else None
        rule.maximum = float(form_data.maximum) if form_data.maximum.strip() else None
        rule.enum = form_data.get_enum()
        rule.reference = form_data.reference.strip() or None
        rule.unique_within = form_data.unique_within.strip() or None
        rule.min_items = int(form_data.min_items) if form_data.min_items.strip() else None
        rule.max_items = int(form_data.max_items) if form_data.max_items.strip() else None

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

    # =========================================================================
    # Preview and Export Routes
    # =========================================================================

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

    # =========================================================================
    # Member Management Routes
    # =========================================================================

    async def _get_spec_members_html(
        request: Request,
        draft_id: str,
        session: AsyncSession,
        keycloak_sub: str,
    ) -> HTMLResponse:
        """Render the member list partial for a spec draft."""
        # Get draft to find owner
        draft_result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = draft_result.scalar_one_or_none()

        draft_owner = None
        if draft:
            owner_result = await session.execute(
                select(User).where(User.keycloak_id == draft.user_id)
            )
            draft_owner = owner_result.scalar_one_or_none()

        # Get members
        result = await session.execute(
            select(SpecDraftMember)
            .where(SpecDraftMember.spec_draft_id == draft_id)
            .options(selectinload(SpecDraftMember.user))
        )
        members = list(result.scalars().all())

        # Get current user's database ID
        current_user_result = await session.execute(
            select(User).where(User.keycloak_id == keycloak_sub)
        )
        current_db_user = current_user_result.scalar_one_or_none()

        return templates.TemplateResponse(
            request,
            "partials/spec_draft_members.html",
            {
                "members": members,
                "draft_id": draft_id,
                "draft_owner": draft_owner,
                "current_user_id": current_db_user.id if current_db_user else None,
            },
        )

    @router.post("/{draft_id}/members", response_class=HTMLResponse)
    async def add_spec_member(
        request: Request,
        draft_id: str,
        email: Annotated[str, Form()],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Add a member to a spec draft by email."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return HTMLResponse("<div class='error'>Login required</div>", status_code=401)

        # Find user by email
        result = await session.execute(select(User).where(User.email == email))
        target_user = result.scalar_one_or_none()

        if not target_user:
            # Return error message - user must log in first
            response = await _get_spec_members_html(request, draft_id, session, keycloak_sub)
            msg = "User not found. They must log in first before you can share."
            response.headers["HX-Trigger"] = (
                f'{{"showToast": {{"message": "{msg}", "type": "error"}}}}'
            )
            return response

        # Check if already a member
        existing = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == target_user.id,
            )
        )
        if existing.scalar_one_or_none():
            return await _get_spec_members_html(request, draft_id, session, keycloak_sub)

        # Add member with viewer role by default
        member = SpecDraftMember(
            spec_draft_id=draft_id,
            user_id=target_user.id,
            role=SpecDraftRole.VIEWER,
        )
        session.add(member)
        await session.commit()

        return await _get_spec_members_html(request, draft_id, session, keycloak_sub)

    @router.patch("/{draft_id}/members/{user_id}", response_class=HTMLResponse)
    async def update_spec_member_role(
        request: Request,
        draft_id: str,
        user_id: str,
        role: Annotated[str, Form()],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Update a member's role in a spec draft."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return HTMLResponse("<div class='error'>Login required</div>", status_code=401)

        result = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()

        if member:
            member.role = SpecDraftRole(role)
            await session.commit()

        return await _get_spec_members_html(request, draft_id, session, keycloak_sub)

    @router.delete("/{draft_id}/members/{user_id}", response_class=HTMLResponse)
    async def remove_spec_member(
        request: Request,
        draft_id: str,
        user_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Remove a member from a spec draft."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return HTMLResponse("<div class='error'>Login required</div>", status_code=401)

        result = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()

        if member:
            await session.delete(member)
            await session.commit()

        return await _get_spec_members_html(request, draft_id, session, keycloak_sub)

    @router.delete("/{draft_id}/leave", response_class=HTMLResponse)
    async def leave_spec(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Leave a spec draft as owner (transfer ownership)."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return HTMLResponse("<div class='error'>Login required</div>", status_code=401)

        # Get draft
        draft_result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = draft_result.scalar_one_or_none()
        if not draft or draft.user_id != keycloak_sub:
            return HTMLResponse("<div class='error'>Access denied</div>", status_code=403)

        # Check if there's another owner in members
        members_result = await session.execute(
            select(SpecDraftMember)
            .where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.role == SpecDraftRole.OWNER,
            )
            .options(selectinload(SpecDraftMember.user))
        )
        owner_members = list(members_result.scalars().all())

        if not owner_members:
            response = await _get_spec_members_html(request, draft_id, session, keycloak_sub)
            msg = "Cannot leave: assign another owner first."
            response.headers["HX-Trigger"] = (
                f'{{"showToast": {{"message": "{msg}", "type": "error"}}}}'
            )
            return response

        # Transfer ownership to the first owner member
        new_owner = owner_members[0]
        draft.user_id = new_owner.user.keycloak_id

        # Remove the new owner from members (they're now the primary owner)
        await session.delete(new_owner)
        await session.commit()

        # Redirect to spec list since user no longer owns this
        return HTMLResponse(
            content='<div hx-redirect="/hub/spec-builder"></div>',
            headers={"HX-Redirect": "/hub/spec-builder"},
        )

    # =========================================================================
    # Comment Routes
    # =========================================================================

    async def _get_spec_comments_html(
        request: Request,
        draft_id: str,
        session: AsyncSession,
        keycloak_sub: str,
    ) -> HTMLResponse:
        """Render the spec comments list partial."""
        # Get database user ID from keycloak sub
        user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_sub))
        db_user = user_result.scalar_one_or_none()
        current_user_id = db_user.id if db_user else None

        # Get top-level comments (no parent) with nested relationships
        result = await session.execute(
            select(SpecComment)
            .where(SpecComment.spec_draft_id == draft_id, SpecComment.parent_id.is_(None))
            .options(
                selectinload(SpecComment.user),
                selectinload(SpecComment.reactions),
                selectinload(SpecComment.replies).selectinload(SpecComment.user),
                selectinload(SpecComment.replies).selectinload(SpecComment.reactions),
            )
            .order_by(SpecComment.created_at.desc())
        )
        comments = list(result.scalars().all())

        return templates.TemplateResponse(
            request,
            "partials/spec_comments_list.html",
            {
                "comments": comments,
                "draft_id": draft_id,
                "current_user_id": current_user_id,
            },
        )

    @router.get("/{draft_id}/comments", response_class=HTMLResponse)
    async def get_spec_comments(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Get all comments for a spec draft."""
        try:
            user_id, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        return await _get_spec_comments_html(request, draft_id, session, user_id)

    @router.post("/{draft_id}/comments", response_class=HTMLResponse)
    async def add_spec_comment(
        request: Request,
        draft_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        content: str = Form(...),
        parent_id: str | None = Form(None),
    ) -> Response:
        """Add a comment to a spec draft."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Get user from database by keycloak sub
        user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_sub))
        db_user = user_result.scalar_one_or_none()

        if not db_user:
            return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

        comment = SpecComment(
            spec_draft_id=draft_id,
            user_id=db_user.id,
            parent_id=parent_id if parent_id else None,
            content=content.strip(),
        )
        session.add(comment)
        await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, keycloak_sub)

    @router.delete("/{draft_id}/comments/{comment_id}", response_class=HTMLResponse)
    async def delete_spec_comment(
        request: Request,
        draft_id: str,
        comment_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Delete a spec comment (only by owner)."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Get user from database
        user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_sub))
        db_user = user_result.scalar_one_or_none()

        if not db_user:
            return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

        # Find comment and verify ownership
        result = await session.execute(select(SpecComment).where(SpecComment.id == comment_id))
        comment = result.scalar_one_or_none()

        if comment and comment.user_id == db_user.id:
            await session.delete(comment)
            await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, keycloak_sub)

    @router.post("/{draft_id}/comments/{comment_id}/react", response_class=HTMLResponse)
    async def react_to_spec_comment(
        request: Request,
        draft_id: str,
        comment_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        reaction: str = Form(...),
    ) -> Response:
        """Add or toggle a reaction on a spec comment."""
        try:
            keycloak_sub, _ = await get_user_context(request, session)
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Get user from database
        user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_sub))
        db_user = user_result.scalar_one_or_none()

        if not db_user:
            return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

        # Check for existing reaction
        existing_result = await session.execute(
            select(SpecCommentReaction).where(
                SpecCommentReaction.comment_id == comment_id,
                SpecCommentReaction.user_id == db_user.id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        reaction_type = ReactionType(reaction)

        if existing:
            if existing.reaction == reaction_type:
                # Toggle off - remove reaction
                await session.delete(existing)
            else:
                # Change reaction type
                existing.reaction = reaction_type
        else:
            # Add new reaction
            new_reaction = SpecCommentReaction(
                comment_id=comment_id,
                user_id=db_user.id,
                reaction=reaction_type,
            )
            session.add(new_reaction)

        await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, keycloak_sub)

    return router
