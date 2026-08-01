"""Advisory findings must be visible to someone building a spec in the browser.

metaseed reports two kinds of finding about a draft specification: ``validate()``
returns defects, and ``warnings()`` returns advisories -- findings that never
affect validity, such as an identifier inferred onto an optional free-text
field. The advisories were returned by the MCP ``spec_validate`` tool and by
nothing else, so a browser author never saw them. These tests drive the spec
builder's own Checks route, and pin that an advisory neither reports the draft
as invalid nor stops it being published.
"""

from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft, Tenant, User
from metaseed_hub.ui.spec_builder.access import DraftContext, load_state_for_draft
from metaseed_hub.ui.spec_builder.routes.check_routes import register_check_routes
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
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


def _spec_with(fields: list[FieldSpec], root_entity: str = "Sample") -> ProfileSpec:
    """A one-entity specification whose identifier situation is under test."""
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity=root_entity,
        entities={"Sample": EntityDefSpec(description="sample", fields=fields)},
    )


def _inferred_identifier_spec() -> ProfileSpec:
    """A draft whose identifier is inferred onto an optional free-text field."""
    return _spec_with([FieldSpec(name="notes", type=FieldType.STRING)])


def _declared_identifier_spec() -> ProfileSpec:
    """The same draft with the identifier declared, so nothing is inferred."""
    return _spec_with(
        [FieldSpec(name="notes", type=FieldType.STRING, is_identifier=True)],
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


async def _checks_body(session: AsyncSession, spec: ProfileSpec) -> str:
    """The Checks panel's HTML for a draft holding ``spec``.

    Entities are unescaped so an assertion can quote the message a reader sees;
    the class names the styling assertions look for are unaffected.
    """
    draft, tenant, owner = await _owned_draft(session, spec)
    ctx = await _context(session, draft, tenant, owner)
    endpoint = _endpoint(register_check_routes, "/checks", "GET")
    response = await endpoint(request=_request("GET"), ctx=ctx)
    return html.unescape(response.body.decode())


class TestAdvisoriesAreShown:
    """An inferred identifier is reported, as advice rather than as a defect."""

    async def test_the_advisory_text_is_rendered(self, session: AsyncSession) -> None:
        body = await _checks_body(session, _inferred_identifier_spec())

        assert "identifier is inferred as 'notes'" in body
        assert "is_identifier" in body, "the advisory must name the marker that settles it"

    async def test_the_advisory_is_marked_as_advice(self, session: AsyncSession) -> None:
        body = await _checks_body(session, _inferred_identifier_spec())

        assert "validation-advisory" in body, "advisories are not visually distinguished"

    async def test_an_advisory_does_not_report_the_spec_as_invalid(
        self, session: AsyncSession
    ) -> None:
        body = await _checks_body(session, _inferred_identifier_spec())

        assert "validation-success" in body, "a spec that builds must read as valid"
        assert "validation-error" not in body, "an advisory must not look like a defect"

    async def test_a_declared_identifier_produces_no_advisory(self, session: AsyncSession) -> None:
        body = await _checks_body(session, _declared_identifier_spec())

        assert "identifier is inferred" not in body
        assert "validation-advisory" not in body
        assert "validation-success" in body


class TestProblemsStayDistinct:
    """A real defect is reported separately, and as a defect."""

    async def test_a_problem_is_reported_as_an_error(self, session: AsyncSession) -> None:
        spec = _spec_with(
            [FieldSpec(name="sample_id", type=FieldType.STRING, required=True)],
            root_entity="Ghost",
        )

        body = await _checks_body(session, spec)

        assert "validation-error" in body
        assert "root_entity 'Ghost' is not a defined entity" in body
        assert "validation-success" not in body


class TestAdvisoriesDoNotBlockPublishing:
    """The draft builds, so an advisory must not stand in the way of a release."""

    async def test_a_draft_with_an_advisory_still_publishes(self, session: AsyncSession) -> None:
        draft, tenant, owner = await _owned_draft(session, _inferred_identifier_spec())
        publish = _endpoint(register_draft_routes, "/{draft_id}/publish", "POST")

        response = await publish(
            request=_request("POST"),
            draft_id=draft.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
        )

        assert "published successfully" in response.body.decode()
