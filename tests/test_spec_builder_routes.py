"""Route-level input validation and cleanup behavior in the spec builder.

Pins three fixes: client-controlled enum strings (member role, comment
reaction) must produce a 400 instead of an unhandled ValueError, malformed
numeric rule input must surface as a form error instead of a 500, and deleting
an entity must not leave dangling references in other entities or rules.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import (
    EntityDefSpec,
    FieldSpec,
    FieldType,
    ProfileSpec,
    ValidationRuleSpec,
)
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft, SpecDraftMember, SpecDraftRole, Tenant, User
from metaseed_hub.ui.spec_builder.access import DraftContext, load_state_for_draft
from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes
from metaseed_hub.ui.spec_builder.routes.entity_routes import register_entity_routes
from metaseed_hub.ui.spec_builder.routes.member_routes import register_member_routes
from metaseed_hub.ui.spec_builder.routes.rule_routes import register_rule_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec_draft, make_tenant, make_user

_TEMPLATES = Jinja2Templates(directory="src/metaseed_hub/ui/templates")


def _endpoint(register: Any, path_suffix: str, method: str) -> Any:
    """The registered endpoint function for a spec-builder route."""
    router = APIRouter()
    register(router, _TEMPLATES)
    for route in router.routes:
        if route.path.endswith(path_suffix) and method in route.methods:  # type: ignore[attr-defined]
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no route {method} ...{path_suffix}")


def _request(method: str) -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


def _rule_form(**overrides: str) -> dict[str, str]:
    """Every form field of the rule update route, so direct endpoint calls do
    not fall back to the FastAPI ``Form`` sentinel defaults."""
    values = {
        "name": "sample_only",
        "description": "",
        "applies_to": "all",
        "field": "",
        "condition": "",
        "pattern": "",
        "minimum": "",
        "maximum": "",
        "enum_values": "",
        "reference": "",
        "unique_within": "",
        "min_items": "",
        "max_items": "",
    }
    values.update(overrides)
    return values


def _cross_referencing_spec() -> ProfileSpec:
    """A spec where Study references Sample in every reference-carrying slot."""
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity="Study",
        entities={
            "Study": EntityDefSpec(
                description="study",
                fields=[
                    FieldSpec(name="samples", type=FieldType.LIST, items="Sample"),
                    FieldSpec(name="sample_ref", type=FieldType.STRING, reference="Sample.id"),
                    FieldSpec(name="parent", type=FieldType.STRING, parent_ref="Sample.id"),
                ],
            ),
            "Sample": EntityDefSpec(description="sample", fields=[]),
        },
        validation_rules=[
            ValidationRuleSpec(name="sample_only", description="", applies_to="Sample"),
            ValidationRuleSpec(name="both", description="", applies_to=["Sample", "Study"]),
            ValidationRuleSpec(name="everyone", description="", applies_to="all"),
        ],
    )


async def _owned_draft(session: AsyncSession, spec: ProfileSpec) -> tuple[SpecDraft, Tenant, User]:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant, email="owner@example.org")
    session.add(owner)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant,
        user=owner,
        name="demo",
        spec_data=SpecBuilderState(spec=spec).to_dict(),
    )
    session.add(draft)
    await session.commit()
    return draft, tenant, owner


async def _context(
    session: AsyncSession, draft: SpecDraft, tenant: Tenant, user: User
) -> DraftContext:
    builder, loaded = await load_state_for_draft(session, draft.id, user.id)
    return DraftContext(builder=builder, draft=loaded, user_id=user.id, tenant_id=tenant.id)


class TestInvalidEnumInput:
    """Client-controlled enum strings must fail as validation errors."""

    async def test_an_unknown_member_role_returns_400(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        member_user = make_user(tenant=tenant, email="member@example.org")
        session.add(member_user)
        await session.flush()
        session.add(
            SpecDraftMember(
                spec_draft_id=draft.id, user_id=member_user.id, role=SpecDraftRole.VIEWER
            )
        )
        await session.commit()
        endpoint = _endpoint(register_member_routes, "/members/{member_user_id}", "PATCH")

        response = await endpoint(
            request=_request("PATCH"),
            draft_id=draft.id,
            member_user_id=member_user.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
            role="superuser",
        )

        assert response.status_code == 400
        await session.refresh(draft)
        member = await session.get(SpecDraftMember, (draft.id, member_user.id))
        assert member is not None
        assert member.role is SpecDraftRole.VIEWER, "the stored role must be unchanged"

    async def test_an_unknown_reaction_returns_400(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        endpoint = _endpoint(register_comment_routes, "/{comment_id}/react", "POST")

        response = await endpoint(
            request=_request("POST"),
            draft_id=draft.id,
            comment_id="irrelevant",
            session=session,
            user_ctx=(owner.id, tenant.id),
            reaction="explode",
        )

        assert response.status_code == 400


class TestRuleFormValidation:
    """Malformed rule input must be a form error, not a 500."""

    async def test_a_non_numeric_bound_is_a_form_error(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        ctx = await _context(session, draft, tenant, owner)
        endpoint = _endpoint(register_rule_routes, "/validation-rule/{idx}", "PUT")

        response = await endpoint(
            request=_request("PUT"),
            idx=0,
            ctx=ctx,
            session=session,
            **_rule_form(minimum="abc"),
        )

        assert response.status_code == 200
        assert "Minimum must be a number" in response.body.decode()
        assert ctx.spec.validation_rules[0].minimum is None, "the rule must be unchanged"

    async def test_an_empty_rule_name_is_rejected_on_update(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        ctx = await _context(session, draft, tenant, owner)
        endpoint = _endpoint(register_rule_routes, "/validation-rule/{idx}", "PUT")

        response = await endpoint(
            request=_request("PUT"),
            idx=0,
            ctx=ctx,
            session=session,
            **_rule_form(name="   "),
        )

        assert "Rule name is required" in response.body.decode()
        assert ctx.spec.validation_rules[0].name == "sample_only"

    async def test_valid_bounds_are_stored(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        ctx = await _context(session, draft, tenant, owner)
        endpoint = _endpoint(register_rule_routes, "/validation-rule/{idx}", "PUT")

        await endpoint(
            request=_request("PUT"),
            idx=0,
            ctx=ctx,
            session=session,
            **_rule_form(minimum="1.5", max_items="4"),
        )

        rule = ctx.spec.validation_rules[0]
        assert rule.minimum == 1.5
        assert rule.max_items == 4


class TestEntityDeleteCleanup:
    """Deleting an entity must remove every reference to it."""

    @pytest.fixture
    async def deleted_sample(self, session: AsyncSession) -> tuple[DraftContext, AsyncSession]:
        draft, tenant, owner = await _owned_draft(session, _cross_referencing_spec())
        ctx = await _context(session, draft, tenant, owner)
        endpoint = _endpoint(register_entity_routes, "/entity/{name}", "DELETE")
        await endpoint(request=_request("DELETE"), name="Sample", ctx=ctx, session=session)
        return ctx, session

    async def test_field_references_are_cleared(
        self, deleted_sample: tuple[DraftContext, AsyncSession]
    ) -> None:
        ctx, _session = deleted_sample
        fields = {f.name: f for f in ctx.spec.entities["Study"].fields}
        assert fields["samples"].items is None
        assert fields["sample_ref"].reference is None
        assert fields["parent"].parent_ref is None

    async def test_rules_no_longer_name_the_entity(
        self, deleted_sample: tuple[DraftContext, AsyncSession]
    ) -> None:
        ctx, _session = deleted_sample
        rules = {r.name: r for r in ctx.spec.validation_rules}
        assert "sample_only" not in rules, "a rule applying only to the entity is dropped"
        assert rules["both"].applies_to == ["Study"]
        assert rules["everyone"].applies_to == "all"

    async def test_the_cleanup_is_persisted(
        self, deleted_sample: tuple[DraftContext, AsyncSession]
    ) -> None:
        ctx, session = deleted_sample
        await session.refresh(ctx.draft)
        stored = ctx.draft.spec_data["spec"]
        assert "Sample" not in stored["entities"]
        assert all(
            "Sample" not in str(rule.get("applies_to")) for rule in stored["validation_rules"]
        )
