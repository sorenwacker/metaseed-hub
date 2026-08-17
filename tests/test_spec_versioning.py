"""Publishing is the release event, so the version claim is checked there.

A specification's version is a promise about compatibility: MAJOR means datasets
valid under the previous version may now fail, MINOR means they stay valid.
Nothing enforced that promise, so a breaking edit could ship as a MINOR bump and
break every dataset built on the specification without warning.

Saving a draft is not the moment to check -- an author is allowed to be
mid-thought. Publishing is, which is why the gate lives in the hub's publish
route rather than in metaseed's save path. The comparison itself is metaseed's
(``compare_specs``/``required_bump``); these tests pin the hub's use of it, the
content hash it records, and what happens when a stored version predates the
MAJOR.MINOR rule.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs import content_hash, short_hash
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecDraft, SpecStatus
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec, make_spec_draft, make_tenant, make_user

_TEMPLATES = Jinja2Templates(directory="src/metaseed_hub/ui/templates")
# The page templates extend base.html, which calls these. The app registers them
# in create_ui_app; a direct endpoint call has to supply them itself.
_TEMPLATES.env.globals["get_repo_stars"] = lambda *_: None
_TEMPLATES.env.globals["get_metaseed_stars"] = lambda *_: None
_TEMPLATES.env.globals["is_admin"] = lambda *_: False


def _endpoint(path_suffix: str, method: str) -> Any:
    """The registered draft-route endpoint for a path suffix and method."""
    router = APIRouter()
    register_draft_routes(router, _TEMPLATES)
    for route in router.routes:
        if route.path.endswith(path_suffix) and method in route.methods:  # type: ignore[attr-defined]
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no route {method} ...{path_suffix}")


def _request(method: str = "POST") -> Request:
    # query_string included because every real ASGI request carries one, and a
    # route reading request.query_params must not fail only against the double.
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


def _spec(version: str, *, name: str = "cinema", tissue_required: bool = False) -> ProfileSpec:
    """A one-entity profile whose ``tissue`` field is optional or required."""
    return ProfileSpec(
        name=name,
        version=version,
        root_entity="Sample",
        entities={
            "Sample": EntityDefSpec(
                description="a sample",
                fields=[
                    FieldSpec(name="id", type=FieldType.STRING, required=True),
                    FieldSpec(name="tissue", type=FieldType.STRING, required=tissue_required),
                ],
            )
        },
        validation_rules=[],
    )


def _stored(spec: ProfileSpec) -> dict[str, Any]:
    """The ``spec_data`` envelope the hub stores a spec in."""
    state = SpecBuilderState()
    state.spec = spec
    return state.to_dict()


async def _account(session: AsyncSession, slug: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=f"{slug}@example.org")
    session.add(user)
    await session.commit()
    return tenant, user


async def _published(session: AsyncSession, tenant, user, spec: ProfileSpec) -> Spec:
    row = make_spec(
        tenant=tenant,
        created_by=user,
        name=spec.name,
        version=spec.version,
        spec_data=_stored(spec),
    )
    session.add(row)
    await session.commit()
    return row


async def _draft(session: AsyncSession, tenant, user, spec: ProfileSpec) -> SpecDraft:
    row = make_spec_draft(
        tenant=tenant,
        user=user,
        name=spec.name,
        version=spec.version,
        spec_data=_stored(spec),
    )
    session.add(row)
    await session.commit()
    return row


async def _publish(session: AsyncSession, draft: SpecDraft, user) -> str:
    """Drive the publish route and return the rendered body."""
    publish = _endpoint("/{draft_id}/publish", "POST")
    response = await publish(_request(), draft.id, session, (user.id, draft.tenant_id))
    return response.body.decode()


class TestTheBumpGate:
    """What the gate refuses, and what it must leave alone."""

    async def test_a_breaking_change_declared_as_a_minor_bump_is_refused(
        self, session: AsyncSession
    ) -> None:
        """Making a field required invalidates datasets that omitted it."""
        tenant, user = await _account(session, "gate-refuse")
        await _published(session, tenant, user, _spec("1.0"))
        draft = await _draft(session, tenant, user, _spec("1.1", tissue_required=True))

        body = await _publish(session, draft, user)

        assert "became required" in body, body
        assert "2.0" in body, "the message must name the version to declare instead"
        assert (
            await session.execute(select(Spec).where(Spec.version == "1.1"))
        ).scalar_one_or_none() is None
        assert await session.get(SpecDraft, draft.id) is not None, "the draft was consumed anyway"

    async def test_a_sufficient_major_bump_publishes(self, session: AsyncSession) -> None:
        """The gate refuses a dishonest claim, not a breaking change."""
        tenant, user = await _account(session, "gate-major")
        await _published(session, tenant, user, _spec("1.0"))
        draft = await _draft(session, tenant, user, _spec("2.0", tissue_required=True))

        body = await _publish(session, draft, user)

        assert "published successfully" in body, body
        published = (
            await session.execute(select(Spec).where(Spec.name == "cinema", Spec.version == "2.0"))
        ).scalar_one()
        assert published.status == SpecStatus.PUBLISHED

    async def test_a_compatible_change_publishes_as_a_minor_bump(
        self, session: AsyncSession
    ) -> None:
        """Nothing breaking, so a MINOR bump is an honest claim."""
        tenant, user = await _account(session, "gate-minor")
        await _published(session, tenant, user, _spec("1.0"))
        compatible = _spec("1.1")
        compatible.entities["Sample"].fields.append(
            FieldSpec(name="notes", type=FieldType.STRING, required=False)
        )
        draft = await _draft(session, tenant, user, compatible)

        body = await _publish(session, draft, user)

        assert "published successfully" in body, body

    async def test_a_new_profile_name_has_nothing_to_compare_against(
        self, session: AsyncSession
    ) -> None:
        """An unrelated name must not be gated by someone else's history."""
        tenant, user = await _account(session, "gate-newname")
        await _published(session, tenant, user, _spec("9.9", name="unrelated"))
        draft = await _draft(session, tenant, user, _spec("1.1", tissue_required=True))

        body = await _publish(session, draft, user)

        assert "published successfully" in body, body

    async def test_the_comparison_uses_the_latest_published_version(
        self, session: AsyncSession
    ) -> None:
        """1.10 is after 1.9, so the latest is not the lexicographic maximum."""
        tenant, user = await _account(session, "gate-latest")
        await _published(session, tenant, user, _spec("1.9", tissue_required=True))
        await _published(session, tenant, user, _spec("1.10"))
        draft = await _draft(session, tenant, user, _spec("1.11", tissue_required=True))

        body = await _publish(session, draft, user)

        assert "became required" in body, (
            "compared against 1.9 (where tissue was already required) instead of 1.10"
        )


class TestTheContentHash:
    """A version says how a spec relates to its predecessor; the hash names it."""

    async def test_publishing_records_the_content_hash(self, session: AsyncSession) -> None:
        tenant, user = await _account(session, "hash-publish")
        spec = _spec("1.0")
        draft = await _draft(session, tenant, user, spec)

        await _publish(session, draft, user)

        published = (await session.execute(select(Spec).where(Spec.name == "cinema"))).scalar_one()
        assert published.content_hash == content_hash(spec)

    async def test_the_view_page_shows_the_short_hash(self, session: AsyncSession) -> None:
        """Provenance on the page that shows a published spec's identity."""
        tenant, user = await _account(session, "hash-view")
        spec = _spec("1.0")
        row = await _published(session, tenant, user, spec)

        view = _endpoint("/spec/{spec_id}", "GET")
        response = await view(_request("GET"), row.id, session, (user.id, tenant.id))

        assert short_hash(spec) in response.body.decode()


class TestMalformedStoredVersions:
    """A version stored before the MAJOR.MINOR rule must not brick a page."""

    async def test_opening_a_published_spec_reports_a_fixable_problem(
        self, session: AsyncSession
    ) -> None:
        from metaseed_hub.ui.spec_builder.versioning import SpecVersionError

        tenant, user = await _account(session, "bad-version-spec")
        stored = _stored(_spec("1.0"))
        stored["spec"]["version"] = "1.0.0"
        row = make_spec(
            tenant=tenant, created_by=user, name="cinema", version="1.0.0", spec_data=stored
        )
        session.add(row)
        await session.commit()

        view = _endpoint("/spec/{spec_id}", "GET")
        with pytest.raises(SpecVersionError) as caught:
            await view(_request("GET"), row.id, session, (user.id, tenant.id))

        assert "1.0.0" in str(caught.value)
        assert "MAJOR.MINOR" in str(caught.value)

    async def test_opening_a_draft_reports_a_fixable_problem(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.spec_builder.access import load_state_for_draft
        from metaseed_hub.ui.spec_builder.versioning import SpecVersionError

        tenant, user = await _account(session, "bad-version-draft")
        stored = _stored(_spec("1.0"))
        stored["spec"]["version"] = "draft"
        row = make_spec_draft(
            tenant=tenant, user=user, name="cinema", version="draft", spec_data=stored
        )
        session.add(row)
        await session.commit()

        with pytest.raises(SpecVersionError) as caught:
            await load_state_for_draft(session, row.id, user.id)

        assert "draft" in str(caught.value)
        assert "MAJOR.MINOR" in str(caught.value)

    def test_the_problem_renders_as_a_page_rather_than_a_server_error(self) -> None:
        """Registered app-wide, so no route has to remember to catch it."""
        from metaseed_hub.ui.spec_builder.versioning import (
            SpecVersionError,
            handle_spec_version_error,
        )

        response = handle_spec_version_error(
            _request("GET"), SpecVersionError(version="1.0.0", subject="cinema")
        )

        assert response.status_code == 400
        body = response.body.decode()
        assert "1.0.0" in body
        assert "MAJOR.MINOR" in body

    def test_the_handler_is_registered_on_the_app(self) -> None:
        from metaseed_hub.main import create_app
        from metaseed_hub.ui.spec_builder.versioning import SpecVersionError

        app = create_app()
        hub = next(r for r in app.routes if getattr(r, "path", "") == "/hub")
        assert SpecVersionError in hub.app.exception_handlers  # type: ignore[attr-defined]
