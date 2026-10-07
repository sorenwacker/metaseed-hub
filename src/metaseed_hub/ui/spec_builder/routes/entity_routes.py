"""Entity CRUD routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from metaseed.seek.roles import SEEK_ROLES
from metaseed.specs.builder import SpecBuilder
from metaseed.specs.schema import EntityDefSpec, FieldType, SeekEntityConfig, ValidationRuleSpec

from metaseed_hub.ui.spec_builder_helpers import validate_entity_name

from ._common import DraftContextDep, SessionDep

__all__ = ["register_entity_routes"]


def register_entity_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register entity CRUD routes."""
    # The entity editor lists the roles; the tuple is SEEK vocabulary that lives
    # with metaseed's exporter, so the template reads it from here rather than
    # from a copy.
    templates.env.globals["seek_roles"] = SEEK_ROLES

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
        seek_role: str = Form(""),
        seek_template: str = Form(""),
        seek_extended_metadata: str = Form(""),
    ) -> HTMLResponse:
        """Update entity metadata including rename and the SEEK mapping."""
        if name not in ctx.spec.entities:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")

        entity = ctx.spec.entities[name]
        # Checked before anything is written, like the rename below: the role
        # arrives from a form, so it is whatever the browser sent.
        role = seek_role.strip() or None
        if role is not None and role not in SEEK_ROLES:
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
                    "error": f"Unknown SEEK role '{role}'; one of {', '.join(SEEK_ROLES)}",
                },
            )
        # The rename is validated before any field is written: assigning the
        # description or ontology term first left a rejected rename with the
        # cached entity already mutated.
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

            # The library's rename, not a copy of it: the hand-written one had
            # drifted -- it moved the entity to the end of the mapping and left
            # a rule's ``reference`` naming the old entity, so the same draft
            # renamed here and through MCP ended up with different content.
            SpecBuilder.from_spec(ctx.spec).rename_entity(name, new_name)
            final_name = new_name
            if ctx.builder.editing_entity == name:
                ctx.builder.editing_entity = new_name

        # Applied only now that the rename (if any) succeeded.
        entity.description = description.strip()
        entity.ontology_term = ontology_term.strip() or None
        # The editor shows three of the four SEEK settings; the groups it does
        # not show are carried over, so saving the form cannot drop them.
        groups = entity.seek.extended_metadata_groups if entity.seek else None
        template = seek_template.strip() or None
        extended_metadata = seek_extended_metadata.strip() or None
        entity.seek = (
            SeekEntityConfig(
                role=role,
                template=template,
                extended_metadata=extended_metadata,
                extended_metadata_groups=groups,
            )
            if any((role, template, extended_metadata, groups))
            else None
        )
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

        # Clear the same cross-entity references the rename branch in
        # update_entity rewrites, so the saved spec never names an entity that
        # no longer exists.
        for other_entity in ctx.spec.entities.values():
            for field in other_entity.fields:
                if field.items == name:
                    field.items = None
                if field.reference and field.reference.startswith(f"{name}."):
                    field.reference = None
                if field.parent_ref and field.parent_ref.startswith(f"{name}."):
                    field.parent_ref = None

        # Drop the deleted entity from validation rules, and drop rules that
        # applied only to it.
        kept_rules: list[ValidationRuleSpec] = []
        for rule in ctx.spec.validation_rules:
            if rule.applies_to == name:
                continue
            if isinstance(rule.applies_to, list) and name in rule.applies_to:
                remaining = [e for e in rule.applies_to if e != name]
                if not remaining:
                    continue
                rule.applies_to = remaining
            # A rule's ``reference`` names an entity too; fields had theirs
            # cleared above while rules kept naming the deleted entity.
            if rule.reference and rule.reference.startswith(f"{name}."):
                rule.reference = None
            kept_rules.append(rule)
        if len(kept_rules) != len(ctx.spec.validation_rules):
            # Rule indices shifted, so any editing pointer into the old list
            # would reference the wrong rule.
            ctx.spec.validation_rules = kept_rules
            ctx.builder.editing_rule_idx = None
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
