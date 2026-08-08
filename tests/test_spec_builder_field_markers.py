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

import pytest
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

    DCAT is a gated plugin, so the editor only writes the marker for a user
    whose group has been granted it. These tests are about the marker, so they
    grant it and let tests/test_features.py cover the gate itself.
    """

    @pytest.fixture(autouse=True)
    def _granted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _all_features(*_args: object, **_kwargs: object) -> set[str]:
            return {"dcat"}

        monkeypatch.setattr(
            "metaseed_hub.ui.spec_builder.routes.field_routes.user_features",
            _all_features,
        )

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


class TestDcatIsGated:
    """DCAT is a plugin, enabled per identity-provider group.

    No fixture grants it here, so the editor sees a user with no features --
    the same position a real user is in before anyone adds them to the group.
    """

    async def test_the_marker_is_not_saved_without_the_feature(self, session: AsyncSession) -> None:
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

        assert ctx.spec.entities["Investigation"].fields[0].dcat is None

    async def test_the_rest_of_the_field_still_saves(self, session: AsyncSession) -> None:
        # The gate skips one marker, not the whole edit.
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued", description="still saved"),
        )

        field = ctx.spec.entities["Investigation"].fields[0]
        assert field.description == "still saved"
        assert field.dcat is None

    async def test_an_existing_marker_survives_losing_the_feature(
        self, session: AsyncSession
    ) -> None:
        # Losing access should hide the field, not destroy what it held.
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        ctx.spec.entities["Investigation"].fields[0].dcat = "dct:issued"
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat=""),
        )

        assert ctx.spec.entities["Investigation"].fields[0].dcat == "dct:issued"

    async def test_the_user_is_told_the_value_was_not_saved(self, session: AsyncSession) -> None:
        # The input cannot be hidden from the hub, so silence would leave the
        # user typing into a box that does nothing.
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        response = await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat="dct:issued"),
        )

        assert "DCAT" in response.body.decode()

    async def test_nothing_is_said_when_no_dcat_value_was_sent(self, session: AsyncSession) -> None:
        # Someone who never touches the field should not be told about a plugin
        # they were not trying to use.
        draft, tenant, owner = await _owned_draft(session)
        ctx = await _context(session, draft, tenant, owner)
        update = _endpoint(register_field_routes, "/field/{idx}", "PUT")

        response = await update(
            request=_request("PUT"),
            entity_name="Investigation",
            idx=0,
            ctx=ctx,
            session=session,
            **_field_form(dcat=""),
        )

        assert "not saved" not in response.body.decode()

    async def test_granting_it_lets_the_marker_through(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The mirror of the first test: same call, feature granted, saved.
        async def _granted(*_args: object, **_kwargs: object) -> set[str]:
            return {"dcat"}

        monkeypatch.setattr(
            "metaseed_hub.ui.spec_builder.routes.field_routes.user_features",
            _granted,
        )
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
