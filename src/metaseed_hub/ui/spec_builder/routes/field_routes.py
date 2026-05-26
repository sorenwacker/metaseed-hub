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
