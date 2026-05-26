"""List, create, and import routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import Spec, SpecDraft, SpecDraftMember, SpecStatus
from metaseed_hub.ui.spec_builder.access import (
    LoginRequiredRedirectError,
    can_access_workspace,
    create_new_draft,
    get_or_create_default_workspace,
    get_user_context,
    get_user_workspaces,
)
from metaseed_hub.ui.spec_builder_helpers import (
    clone_spec,
    create_empty_spec,
    list_available_templates,
    parse_spec_from_yaml,
)

from ._common import SessionDep, render_with_context


def register_list_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register list, new, import, and clone routes."""

    async def render(request: Request, template: str, context: dict[str, object]) -> Response:
        return await render_with_context(templates, request, template, context)

    @router.get("", response_model=None)
    async def spec_builder_list(
        request: Request,
        session: SessionDep,
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

        # Get drafts user owns (user_id from get_user_context is already User.id)
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
        shared_result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.workspace))
            .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(SpecDraftMember.user_id == user_id)
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
        session: SessionDep,
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
        session: SessionDep,
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
        session: SessionDep,
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
        session: SessionDep,
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
        session: SessionDep,
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
