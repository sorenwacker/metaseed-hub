"""Draft editing, publishing, and viewing routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import FieldType, ProfileSpec
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import Dataset, Spec, SpecDraft, SpecDraftMember, SpecStatus, User
from metaseed_hub.ui.spec_builder.access import (
    can_access_tenant,
    can_edit_spec,
    create_new_draft,
    load_state_for_draft,
    save_state_to_draft,
)
from metaseed_hub.ui.spec_builder.cache import state_cache
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from metaseed_hub.ui.spec_builder_helpers import (
    create_empty_spec,
    slugify_spec_name,
    spec_to_yaml,
)

from ._common import DraftContextDep, SessionDep, UserContextDep, create_render_helper

__all__ = ["register_draft_routes"]


def register_draft_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register draft editing, publishing, and viewing routes."""

    render = create_render_helper(templates)

    @router.get("/{draft_id}", response_model=None)
    async def edit_draft(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Edit a specific draft."""
        user_id, tenant_id = user_ctx

        builder, draft = await load_state_for_draft(session, draft_id, user_id)

        if builder.spec is None:
            builder.spec = create_empty_spec()
            await save_state_to_draft(session, builder, draft)

        # Load draft owner - draft.user_id is a FK to users.id
        owner_result = await session.execute(select(User).where(User.id == draft.user_id))
        draft_owner = owner_result.scalar_one_or_none()

        # Check if current user is the owner (both are User.id)
        is_current_user_owner = draft.user_id == user_id

        # Load members for sharing tab
        members_result = await session.execute(
            select(SpecDraftMember)
            .where(SpecDraftMember.spec_draft_id == draft_id)
            .options(selectinload(SpecDraftMember.user))
        )
        members = list(members_result.scalars().all())

        return await render(
            request,
            "spec_builder/base.html",
            {
                "draft": draft,
                "draft_id": draft_id,
                "tenant": draft.tenant,
                "spec": builder.spec,
                "editing_entity": builder.editing_entity,
                "has_unsaved_changes": builder.has_unsaved_changes,
                "template_source": builder.template_source,
                "field_types": [t.value for t in FieldType],
                "members": members,
                "draft_owner": draft_owner,
                "current_user_id": user_id,
                "is_current_user_owner": is_current_user_owner,
            },
        )

    @router.delete("/{draft_id}", response_model=None)
    async def delete_draft(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Delete a draft."""
        user_id, _tenant_id = user_ctx

        result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = result.scalar_one_or_none()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        if draft.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if any datasets are using this spec. Exclude soft-deleted rows,
        # matching every other Dataset query; otherwise a draft whose only
        # referencing datasets were deleted is wrongly reported as in use.
        datasets_result = await session.execute(
            select(Dataset).where(Dataset.spec_draft_id == draft_id, Dataset.deleted_at.is_(None))
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
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Reset a draft to empty state."""
        user_id, _tenant_id = user_ctx
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

    @router.post("/{draft_id}/publish", response_class=HTMLResponse)
    async def publish_draft(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> HTMLResponse:
        """Publish a draft as a spec."""
        user_id, _tenant_id = user_ctx
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
            result = await session.execute(
                select(Spec).where(Spec.id == draft.source_spec_id, Spec.deleted_at.is_(None))
            )
            existing_spec = result.scalar_one_or_none()
            if existing_spec and not await can_edit_spec(session, user_id, existing_spec.id):
                raise HTTPException(status_code=403, detail="Cannot edit this spec")

        spec = Spec(
            tenant_id=draft.tenant_id,
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
        session: SessionDep,
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

    @router.get("/spec/{spec_id}", response_model=None)
    async def view_spec(
        request: Request,
        spec_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """View a published spec (read-only)."""
        user_id, _tenant_id = user_ctx

        result = await session.execute(
            select(Spec)
            .options(selectinload(Spec.tenant), selectinload(Spec.created_by))
            .where(Spec.id == spec_id, Spec.deleted_at.is_(None))
        )
        spec = result.scalar_one_or_none()

        if not spec:
            raise HTTPException(status_code=404, detail="Spec not found")

        if not await can_access_tenant(session, user_id, spec.tenant_id):
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
                "tenant": spec.tenant,
                "can_edit": await can_edit_spec(session, user_id, spec_id),
            },
        )

    @router.post("/spec/{spec_id}/edit", response_model=None)
    async def create_draft_from_spec(
        request: Request,
        spec_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Create a draft from a published spec for editing."""
        user_id, _tenant_id = user_ctx

        result = await session.execute(
            select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
        )
        spec = result.scalar_one_or_none()

        if not spec:
            raise HTTPException(status_code=404, detail="Spec not found")

        if not await can_access_tenant(session, user_id, spec.tenant_id):
            raise HTTPException(status_code=403, detail="Access denied")

        builder = (
            SpecBuilderState.from_dict(spec.spec_data) if spec.spec_data else SpecBuilderState()
        )

        if builder.spec is None:
            raise HTTPException(status_code=400, detail="Invalid spec data")

        draft = await create_new_draft(
            session,
            user_id=user_id,
            tenant_id=spec.tenant_id,
            name=spec.name,
            spec=builder.spec,
            source_spec_id=spec.id,
        )

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

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
        session: SessionDep,
        name: str = Form(""),
        version: str = Form("0.1"),
        display_name: str = Form(""),
        description: str = Form(""),
        ontology: str = Form(""),
        root_entity: str = Form(""),
    ) -> HTMLResponse:
        """Update profile metadata."""
        ctx.spec.name = slugify_spec_name(name)
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

    @router.get("/{draft_id}/export", response_model=None)
    async def export_yaml(
        request: Request,
        draft_id: str,
        session: SessionDep,
    ) -> StreamingResponse | RedirectResponse:
        """Download the spec as a YAML file.

        Handles authentication gracefully - redirects to login instead of 401
        so browser downloads don't fail silently.
        """
        from metaseed_hub.ui.spec_builder.access import (
            LoginRequiredRedirectError,
            get_user_context,
            load_state_for_draft,
        )

        # Handle authentication with redirect instead of 401
        try:
            user_id, _tenant_id = await get_user_context(
                request, session, redirect_on_unauthorized=True
            )
        except LoginRequiredRedirectError:
            return RedirectResponse(url="/hub/auth/login", status_code=302)

        # Load draft state
        try:
            state, draft = await load_state_for_draft(session, draft_id, user_id)
        except HTTPException as e:
            if e.status_code in (401, 403):
                return RedirectResponse(url="/hub/auth/login", status_code=302)
            raise

        if state.spec is None:
            raise HTTPException(status_code=400, detail="No spec in progress")

        yaml_content = spec_to_yaml(state.spec)
        filename = f"{state.spec.name or 'profile'}.yaml"

        return StreamingResponse(
            iter([yaml_content]),
            media_type="application/x-yaml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
