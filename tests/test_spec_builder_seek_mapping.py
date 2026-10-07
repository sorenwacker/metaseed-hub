"""The entity editor shows and sets an entity's SEEK mapping.

The push reads an entity's SEEK role, ISA template and extended metadata type,
and the SEEK controls appear on a dataset only when an entity has the
Investigation role -- yet the builder had no control for any of it. The
settings reached a specification through a YAML import only, and once there
were invisible: an author looking for "the ISA tag for Investigation" found a
field-level dropdown and nothing else. ``docs/spec-builder/index.md#seek-mapping``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.seek.roles import SEEK_ROLES
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec, SeekEntityConfig
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.spec_builder.routes.entity_routes import register_entity_routes
from tests.test_spec_builder_routes import _context, _owned_draft

pytestmark = pytest.mark.asyncio

_TEMPLATES = Jinja2Templates(directory="src/metaseed_hub/ui/templates")


def _endpoint(path_suffix: str, method: str) -> Any:
    router = APIRouter()
    register_entity_routes(router, _TEMPLATES)
    for route in router.routes:
        if route.path.endswith(path_suffix) and method in route.methods:  # type: ignore[attr-defined]
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no route {method} ...{path_suffix}")


def _request(method: str) -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


def _spec() -> ProfileSpec:
    """An Investigation mapped onto SEEK, a Study with a grouped extended
    metadata type, and a Note SEEK does not receive."""
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity="Investigation",
        entities={
            "Investigation": EntityDefSpec(
                description="the investigation",
                fields=[FieldSpec(name="title", type=FieldType.STRING)],
                seek=SeekEntityConfig(role="Investigation"),
            ),
            "Study": EntityDefSpec(
                description="a study",
                fields=[FieldSpec(name="site_latitude", type=FieldType.STRING)],
                seek=SeekEntityConfig(
                    role="Study",
                    extended_metadata="CropXR study",
                    extended_metadata_groups={"site": "location"},
                ),
            ),
            "Note": EntityDefSpec(
                description="a note",
                fields=[FieldSpec(name="text", type=FieldType.STRING)],
            ),
        },
    )


def _update_form(**overrides: str) -> dict[str, str]:
    values = {
        "new_name": "",
        "description": "",
        "ontology_term": "",
        "seek_role": "",
        "seek_template": "",
        "seek_extended_metadata": "",
    }
    values.update(overrides)
    return values


class TestTheSectionIsShown:
    async def test_the_editor_offers_every_role(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        html = (
            await _endpoint("/entity/{name}", "GET")(request=_request("GET"), name="Note", ctx=ctx)
        ).body.decode()

        assert 'data-testid="seek-role"' in html
        for role in SEEK_ROLES:
            assert f'value="{role}"' in html, f"the role {role} is not on offer"
        assert 'data-testid="seek-template"' in html
        assert 'data-testid="seek-extended-metadata"' in html

    async def test_a_mapped_entity_shows_its_mapping(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        html = (
            await _endpoint("/entity/{name}", "GET")(request=_request("GET"), name="Study", ctx=ctx)
        ).body.decode()

        assert 'value="Study" selected' in html
        assert 'value="CropXR study"' in html


class TestTheMappingIsSet:
    async def test_a_role_and_type_are_stored(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        response = await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Note",
            ctx=ctx,
            session=session,
            **_update_form(
                description="a note", seek_role="Assay", seek_extended_metadata="CropXR assay"
            ),
        )

        assert response.status_code == 200
        seek = ctx.spec.entities["Note"].seek
        assert seek is not None
        assert (seek.role, seek.template, seek.extended_metadata) == (
            "Assay",
            None,
            "CropXR assay",
        )

    async def test_a_template_binds_a_sample_level_entity(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Note",
            ctx=ctx,
            session=session,
            **_update_form(seek_role="Sample", seek_template="CropXR source"),
        )

        seek = ctx.spec.entities["Note"].seek
        assert seek is not None and (seek.role, seek.template) == ("Sample", "CropXR source")

    async def test_clearing_everything_removes_the_mapping(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Investigation",
            ctx=ctx,
            session=session,
            **_update_form(description="the investigation"),
        )

        assert ctx.spec.entities["Investigation"].seek is None

    async def test_groups_the_editor_cannot_show_survive_an_update(
        self, session: AsyncSession
    ) -> None:
        """The prefix-to-attribute groups have no control; saving the form must
        not drop what it does not show."""
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Study",
            ctx=ctx,
            session=session,
            **_update_form(
                description="a study", seek_role="Study", seek_extended_metadata="CropXR study v2"
            ),
        )

        seek = ctx.spec.entities["Study"].seek
        assert seek is not None
        assert seek.extended_metadata == "CropXR study v2"
        assert seek.extended_metadata_groups == {"site": "location"}

    async def test_an_unknown_role_is_a_form_error(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)

        response = await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Note",
            ctx=ctx,
            session=session,
            **_update_form(description="changed", seek_role="Experiment"),
        )

        assert response.status_code == 200
        assert "Experiment" in response.body.decode()
        assert ctx.spec.entities["Note"].seek is None
        assert ctx.spec.entities["Note"].description == "a note", "nothing else changed either"

    async def test_the_mapping_is_persisted(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.spec_builder.access import load_state_for_draft

        draft, tenant, owner = await _owned_draft(session, _spec())
        ctx = await _context(session, draft, tenant, owner)
        await _endpoint("/entity/{name}", "PUT")(
            request=_request("PUT"),
            name="Note",
            ctx=ctx,
            session=session,
            **_update_form(seek_role="DataFile", seek_template="CropXR phenotyping data file"),
        )

        builder, _ = await load_state_for_draft(session, draft.id, owner.id)
        seek = builder.spec.entities["Note"].seek
        assert seek is not None and seek.template == "CropXR phenotyping data file"
