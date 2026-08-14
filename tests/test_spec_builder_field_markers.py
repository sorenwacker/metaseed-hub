"""The DCAT marker must be settable from the browser field editor.

metaseed's ``FieldSpec`` carries a ``dcat`` marker naming the DCAT property a
root-entity field supplies on the dataset's catalogue record. Its eleven sibling
markers were already editable in the web field editor; this one was reachable
only over MCP, so a specification authored in the browser could not describe its
dataset to a catalogue at all. These tests drive the field editor's own routes,
so the form wiring is what is exercised, not a helper underneath it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft, Tenant, User
from metaseed_hub.ui.spec_builder.access import DraftContext, load_state_for_draft
from metaseed_hub.ui.spec_builder.routes.field_routes import register_field_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState

# The field form is metaseed's; the hub must load it the way the app does.
from tests.conftest import app_templates
from tests.factories import make_spec_draft, make_tenant, make_user

_TEMPLATES = app_templates()


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


def _field_form(**overrides: Any) -> dict[str, Any]:
    """Every form field the field editor posts, so a direct endpoint call does
    not fall back to the FastAPI ``Form`` sentinel defaults."""
    values: dict[str, Any] = {
        "name": "submission_date",
        "field_type": "string",
        "required": False,
        "description": "",
        "ontology_term": "",
        "ontologies": "",
        "codename": "",
        "items": "",
        "parent_ref": "",
        "pattern": "",
        "min_length": "",
        "max_length": "",
        "minimum": "",
        "maximum": "",
        "min_items": "",
        "max_items": "",
        "enum_values": "",
        "unique_within": "",
        "reference": "",
        "owns": False,
        "is_identifier": False,
        "is_label": False,
        "tier": "",
        "label": "",
        "unit": "",
        "example": "",
        "options": "",
        "dcat": "",
    }
    values.update(overrides)
    return values


def _investigation_spec() -> ProfileSpec:
    """A root entity with one dataset-level field, the case DCAT is meant for."""
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity="Investigation",
        entities={
            "Investigation": EntityDefSpec(
                description="investigation",
                fields=[FieldSpec(name="submission_date", type=FieldType.STRING)],
            )
        },
    )


async def _owned_draft(session: AsyncSession) -> tuple[SpecDraft, Tenant, User]:
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
        spec_data=SpecBuilderState(spec=_investigation_spec()).to_dict(),
    )
    session.add(draft)
    await session.commit()
    return draft, tenant, owner


async def _context(
    session: AsyncSession, draft: SpecDraft, tenant: Tenant, user: User
) -> DraftContext:
    builder, loaded = await load_state_for_draft(session, draft.id, user.id)
    return DraftContext(builder=builder, draft=loaded, user_id=user.id, tenant_id=tenant.id)


class TestDcatMarker:
    """The DCAT property marker, set and re-read through the field editor.

    DCAT is an adapter like ENA and SEEK; its marker writes for every
    signed-in user (the per-group gate is gone).
    """

    async def test_setting_the_marker_stores_it_on_the_field(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        assert ctx.spec.entities["Investigation"].fields[0].dcat == "dct:issued"

    async def test_the_marker_is_persisted_to_the_draft(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        await session.refresh(ctx.draft)
        stored = ctx.draft.spec_data["spec"]["entities"]["Investigation"]["fields"][0]
        assert stored["dcat"] == "dct:issued"

    async def test_the_marker_survives_a_reload_of_the_form(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")
        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        get_form = _endpoint(register_field_routes, "/field/{idx}", "GET")
        response = await get_form(
            request=_request("GET"),
            entity_name="Investigation",
            idx=0,
            ctx=await _context(session, draft, tenant, owner),
        )

        body = response.body.decode()
        assert 'name="dcat"' in body, "the field editor offers no DCAT input"
        assert 'value="dct:issued"' in body, "the stored DCAT property is not shown back"

    async def test_clearing_the_input_removes_the_marker(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")
        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="   "),
        )

        assert ctx.spec.entities["Investigation"].fields[0].dcat is None


class TestDcatIsAPluginNotAGatedFeature:
    """The DCAT marker writes without any grant, like every adapter's fields.

    DCAT is a metaseed adapter — registered beside ENA and SEEK — and no
    other adapter's spec-authoring support sits behind a per-group grant
    (SEEK's isa_tag column never did). The FeatureGrant gate hid the DCAT
    column from every user since it shipped, because nothing ever wrote a
    grant row.
    """

    async def test_the_marker_writes_with_no_grant_anywhere(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        assert ctx.spec.entities["Investigation"].fields[0].dcat == "dct:issued"
