"""List, create, and import routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import Spec, SpecDraft, SpecDraftMember, SpecStatus
from metaseed_hub.ui.spec_builder.access import (
    create_new_draft,
    free_draft_name,
    workspace_owner,
)
from metaseed_hub.ui.spec_builder_helpers import (
    clone_spec,
    create_empty_spec,
    list_available_templates,
    parse_spec_from_yaml,
    slugify_spec_name,
)

from ._common import SessionDep, UserContextDep, create_render_helper

__all__ = ["register_list_routes"]


def register_list_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register list, new, import, and clone routes."""

    render = create_render_helper(templates)

    @router.get("", response_model=None)
    async def spec_builder_list(
        request: Request,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Render the specs list page showing drafts and published specs."""
        user_id, tenant_id = user_ctx

        # Get drafts user owns
        owned_result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.tenant))
            .where(
                SpecDraft.user_id == user_id,
                SpecDraft.tenant_id == tenant_id,
            )
            .order_by(SpecDraft.updated_at.desc())
        )
        owned_drafts = list(owned_result.scalars().all())

        # Get drafts shared with user
        shared_result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.tenant))
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

        # Every published specification, from every workspace. Publishing is
        # how a specification is shared with the other people on the platform;
        # a draft is the private form. Scoping this to the caller's own
        # workspace made publishing a no-op that only its author could observe.
        result = await session.execute(
            select(Spec)
            .options(selectinload(Spec.tenant), selectinload(Spec.created_by))
            .where(
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
            .order_by(Spec.updated_at.desc())
        )
        specs = list(result.scalars().all())

        # Who owns the workspace each spec lives in. Usually the author, but not
        # when a shared draft was published: then the author cannot find their
        # own specification unless the page says whose workspace it is in.
        owners = {}
        for spec in specs:
            if spec.tenant_id not in owners:
                owners[spec.tenant_id] = await workspace_owner(session, spec.tenant_id)

        return await render(
            request,
            "spec_builder/list.html",
            {
                "drafts": drafts,
                "specs": specs,
                "tenant_id": tenant_id,
                "owners": owners,
            },
        )

    @router.get("/new", response_model=None)
    async def new_spec_form(
        request: Request,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Show form to create a new spec draft."""
        user_id, tenant_id = user_ctx

        return await render(
            request,
            "spec_builder/new.html",
            {
                "tenant_id": tenant_id,
                "templates": list_available_templates(),
            },
        )

    @router.post("/new", response_model=None)
    async def create_new_spec(
        request: Request,
        session: SessionDep,
        user_ctx: UserContextDep,
        name: str = Form(""),
        template: str = Form(""),
    ) -> Response:
        """Create a new spec draft."""
        user_id, tenant_id = user_ctx

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
            spec.name = slugify_spec_name(name)

        draft_name = spec.name or "Untitled"

        # A blank name means the draft is named after its template, so using the
        # same template twice would always collide. That is not the user's
        # choice to defend -- give the second one a suffix. A name they typed is
        # a decision, so a clash there is reported rather than silently renamed.
        if not name.strip():
            draft_name = await free_draft_name(
                session, user_id=user_id, tenant_id=tenant_id, wanted=draft_name
            )

        # One draft name per user (uq_spec_drafts_tenant_user_name). Reusing a
        # name is an ordinary mistake, so name it instead of letting the
        # IntegrityError surface as "Error creating specification".
        try:
            draft = await create_new_draft(
                session,
                user_id=user_id,
                tenant_id=tenant_id,
                name=draft_name,
                spec=spec,
                template_source=template_source,
            )
        except IntegrityError:
            await session.rollback()
            # Plain text: the create page posts this with fetch and shows the
            # body, so the reason reaches the user instead of a generic alert.
            return PlainTextResponse(
                f"You already have a specification named '{draft_name}'. "
                "Pick a different name, or open the existing one.",
                status_code=409,
            )

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)

    @router.get("/import", response_model=None)
    async def import_spec_form(
        request: Request,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Show form to import a spec from YAML file."""
        user_id, tenant_id = user_ctx

        return await render(
            request,
            "spec_builder/import.html",
            {"tenant_id": tenant_id},
        )

    @router.post("/import", response_model=None)
    async def import_spec(
        request: Request,
        session: SessionDep,
        user_ctx: UserContextDep,
        spec_file: UploadFile = File(...),
        name: str = Form(""),
    ) -> Response:
        """Import a spec from uploaded YAML file."""
        user_id, tenant_id = user_ctx

        if not spec_file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        if not spec_file.filename.endswith((".yaml", ".yml")):
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "tenant_id": tenant_id,
                    "error": "File must be a YAML file (.yaml or .yml)",
                },
            )

        try:
            content = await spec_file.read()
            yaml_content = content.decode("utf-8")
            spec = parse_spec_from_yaml(yaml_content)
        except UnicodeDecodeError:
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "tenant_id": tenant_id,
                    "error": "File must be UTF-8 encoded",
                },
            )
        except ValueError as e:
            return await render(
                request,
                "spec_builder/import.html",
                {
                    "tenant_id": tenant_id,
                    "error": str(e),
                },
            )

        draft_name = name.strip() if name.strip() else (spec.name if spec.name else "Imported Spec")
        # Importing the same specification twice is ordinary; the second must
        # not be refused because the first took the name.
        draft_name = await free_draft_name(
            session, user_id=user_id, tenant_id=tenant_id, wanted=draft_name
        )

        draft = await create_new_draft(
            session,
            user_id=user_id,
            tenant_id=tenant_id,
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
        user_ctx: UserContextDep,
    ) -> Response:
        """Clone an existing spec as a template."""
        user_id, tenant_id = user_ctx

        try:
            spec = clone_spec(profile, version)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        draft = await create_new_draft(
            session,
            user_id=user_id,
            tenant_id=tenant_id,
            name=await free_draft_name(
                session, user_id=user_id, tenant_id=tenant_id, wanted=spec.name
            ),
            spec=spec,
            template_source=(profile, version),
        )

        return RedirectResponse(url=f"/hub/spec-builder/{draft.id}", status_code=302)
