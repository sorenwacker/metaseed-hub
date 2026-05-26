"""Validation rule CRUD routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import ValidationRuleSpec

from metaseed_hub.ui.spec_builder.forms import ValidationRuleFormData

from ._common import DraftContextDep, SessionDep

__all__ = ["register_rule_routes"]


def register_rule_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register validation rule CRUD routes."""

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
        session: SessionDep,
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
        session: SessionDep,
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
        session: SessionDep,
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
