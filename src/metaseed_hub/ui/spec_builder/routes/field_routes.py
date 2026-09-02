"""Field CRUD routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import FieldSpec, FieldType

from metaseed_hub.ui.spec_builder.forms import FieldFormData
from metaseed_hub.ui.spec_builder_helpers import validate_field_name

from ._common import DraftContextDep, SessionDep

__all__ = ["register_field_routes"]


def register_field_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register field CRUD routes."""

    @router.post("/{draft_id}/entity/{entity_name}/field", response_class=HTMLResponse)
    async def add_field(
        request: Request,
        entity_name: str,
        ctx: DraftContextDep,
        session: SessionDep,
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
                # The template is metaseed's; it names no URL of its own beyond
                # this one, which is per-draft and /hub-prefixed here.
                "entity_url": (f"/hub/spec-builder/{ctx.draft.id}/entity/{entity_name}"),
            },
        )

    @router.put("/{draft_id}/entity/{entity_name}/field/{idx}", response_class=HTMLResponse)
    async def update_field(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: SessionDep,
        name: str = Form(...),
        field_type: str = Form("string"),
        required: bool = Form(False),
        description: str = Form(""),
        ontology_term: str = Form(""),
        ontologies: str = Form(""),
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
        # The DCAT/DCAT-AP property this field supplies on the dataset's
        # catalogue record (spec_version 0.5). Free text: DCAT-AP profiles
        # define properties beyond the ones metaseed resolves, so the input
        # suggests those rather than restricting the field to them.
        dcat: str = Form(""),
        # spec_version 0.6 markers (#137/#143/#98).
        owns: bool = Form(False),
        is_identifier: bool = Form(False),
        is_label: bool = Form(False),
        tier: str = Form(""),
        label: str = Form(""),
        unit: str = Form(""),
        example: str = Form(""),
        options: str = Form(""),
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
            ontologies=ontologies,
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

        # Validate the new name and reject duplicates, mirroring add_field; the
        # update path previously accepted invalid or colliding names that the
        # create path rejects.
        new_name = form_data.name.strip()
        error = validate_field_name(new_name)
        if not error:
            for i, existing in enumerate(entity.fields):
                if i != idx and existing.name == new_name:
                    error = f"Field '{new_name}' already exists"
                    break
        # Parse the field type and constraints before mutating the field, so a
        # client-supplied value outside the enum or malformed numeric input
        # surfaces as a friendly form error instead of a 500 and leaves the
        # field unchanged.
        parsed_type = None
        parsed_constraints = None
        if not error:
            try:
                parsed_type = form_data.get_field_type()
                parsed_constraints = form_data.get_constraints()
            except ValueError as exc:
                error = str(exc)
        if error:
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
                    "error": error,
                },
            )

        field = entity.fields[idx]
        field.name = new_name
        assert parsed_type is not None  # set whenever error is None
        field.type = parsed_type
        field.required = form_data.required
        field.description = form_data.description.strip()
        field.ontology_term = form_data.ontology_term.strip() or None
        # Parse ontologies from comma/newline-separated string to list
        ontologies_str = form_data.ontologies.strip()
        if ontologies_str:
            field.ontologies = [
                o.strip().lower() for o in ontologies_str.replace("\n", ",").split(",") if o.strip()
            ]
        else:
            field.ontologies = None
        field.codename = form_data.codename.strip() or None
        field.items = form_data.items.strip() or None
        field.parent_ref = form_data.parent_ref.strip() or None
        field.unique_within = form_data.unique_within.strip() or None
        field.reference = form_data.reference.strip() or None
        # DCAT is an adapter, like ENA and SEEK, and no adapter's
        # spec-authoring fields sit behind a per-group grant — the
        # FeatureGrant gate that used to guard this write hid the column
        # from every user, because nothing ever wrote a grant row.
        field.dcat = dcat.strip() or None
        # Whole-object replacement, deliberately, and not the merging path
        # ``SpecBuilder.update_field_constraints`` offers: the field form posts
        # every constraint input on every save, so an empty one means "cleared",
        # not "unsupplied". Merging here would make a constraint impossible to
        # remove from the web editor. Metaseed's own field editor replaces for
        # the same reason; the merging path is for partial callers such as the
        # MCP tool, which cannot tell an omitted argument from an empty one.
        field.constraints = parsed_constraints

        # spec_version 0.6 markers. Booleans default to None (not False) so an
        # unset marker is dropped on serialization rather than written as false.
        field.owns = owns or None
        field.is_identifier = is_identifier or None
        field.is_label = is_label or None
        tier_value = tier.strip()
        field.tier = tier_value if tier_value in ("required", "recommended", "optional") else None
        field.label = label.strip() or None
        field.unit = unit.strip() or None
        field.example = example.strip() or None
        field.options = [o.strip() for o in options.split(",") if o.strip()] or None

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
                "notice": None,
            },
        )

    @router.delete("/{draft_id}/entity/{entity_name}/field/{idx}", response_class=HTMLResponse)
    async def delete_field(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: SessionDep,
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

    @router.post(
        "/{draft_id}/entity/{entity_name}/field/{idx}/move-up", response_class=HTMLResponse
    )
    async def move_field_up(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: SessionDep,
    ) -> HTMLResponse:
        """Move a field up in the list."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        entity = ctx.spec.entities[entity_name]
        if idx < 0 or idx >= len(entity.fields):
            raise HTTPException(status_code=404, detail="Field not found")

        if idx > 0:
            # Swap field with previous field
            entity.fields[idx], entity.fields[idx - 1] = (
                entity.fields[idx - 1],
                entity.fields[idx],
            )
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

    @router.post(
        "/{draft_id}/entity/{entity_name}/field/{idx}/move-down", response_class=HTMLResponse
    )
    async def move_field_down(
        request: Request,
        entity_name: str,
        idx: int,
        ctx: DraftContextDep,
        session: SessionDep,
    ) -> HTMLResponse:
        """Move a field down in the list."""
        if entity_name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

        entity = ctx.spec.entities[entity_name]
        if idx < 0 or idx >= len(entity.fields):
            raise HTTPException(status_code=404, detail="Field not found")

        if idx < len(entity.fields) - 1:
            # Swap field with next field
            entity.fields[idx], entity.fields[idx + 1] = (
                entity.fields[idx + 1],
                entity.fields[idx],
            )
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
