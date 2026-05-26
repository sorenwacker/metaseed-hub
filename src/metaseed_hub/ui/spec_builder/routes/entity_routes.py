"""Entity CRUD routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import EntityDefSpec, FieldType

from metaseed_hub.ui.spec_builder_helpers import validate_entity_name

from ._common import DraftContextDep, SessionDep


def register_entity_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register entity CRUD routes."""

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
        session: SessionDep,
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
        session: SessionDep,
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
        session: SessionDep,
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
