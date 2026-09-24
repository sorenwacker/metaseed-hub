"""A published specification has one audience: a collaboration, or everyone.

Publishing used to mean visible to every user of the hub, which left a working
group no way to release a specification to itself. An audience sits between
the private draft and the hub-wide release, and every surface that lists a
published specification has to honour it -- a specification hidden on the
Specs page but offered by the profile picker, the explorer, the REST API or an
agent is not hidden at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.audience import EVERYONE, visible_specs
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.models import Spec
from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.factories import make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
CROPXR = PREFIX + "tudelft:cropxr"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
OTHER = PREFIX + "tudelft:other:members"


def _payload(name: str) -> dict:
    """A minimal profile whose own name matches the row, since the explorer and
    the pickers label a specification from its document, not from the row."""
    return {
        "spec": {
            "name": name,
            "display_name": name,
            "version": "1.0",
            "root_entity": "Sample",
            "entities": {"Sample": {"description": "a sample", "fields": []}},
        }
    }


async def _person(session: AsyncSession, slug: str, urns: list[str]):
    sub = f"{slug}-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, urns)
    await session.commit()
    return tenant, user


async def _published(session: AsyncSession, tenant, user, *, name: str, audience: str | None):
    spec = make_spec(
        tenant=tenant, created_by=user, name=name, version="1.0", spec_data=_payload(name)
    )
    spec.audience_urn = audience
    session.add(spec)
    await session.commit()
    return spec


async def _visible_names(session: AsyncSession, user) -> set[str]:
    rows = await session.execute(select(Spec.name).where(await visible_specs(session, user.id)))
    return set(rows.scalars().all())


async def test_a_collaboration_audience_hides_the_spec_from_everyone_else(
    session: AsyncSession,
) -> None:
    tenant, author = await _person(session, "author", [PHENO])
    _, member = await _person(session, "member", [PHENO])
    _, stranger = await _person(session, "stranger", [OTHER])
    await _published(session, tenant, author, name="for-cropxr", audience=CROPXR)
    await _published(session, tenant, author, name="for-all", audience=EVERYONE)

    assert await _visible_names(session, member) == {"for-cropxr", "for-all"}
    assert await _visible_names(session, stranger) == {"for-all"}


async def test_a_stale_membership_loses_sight_of_it(session: AsyncSession) -> None:
    """The audience is read from the recorded membership, which expires."""
    from datetime import UTC, datetime, timedelta

    tenant, author = await _person(session, "author2", [PHENO])
    _, member = await _person(session, "member2", [PHENO])
    await _published(session, tenant, author, name="for-cropxr", audience=CROPXR)
    await record_memberships(
        session, member.id, [PHENO], seen_at=datetime.now(UTC) - timedelta(days=31)
    )
    await session.commit()

    assert await _visible_names(session, member) == set()


async def test_a_group_audience_is_narrower_than_its_collaboration(
    session: AsyncSession,
) -> None:
    tenant, author = await _person(session, "author3", [PHENO])
    _, sibling = await _person(session, "sibling", [PREFIX + "tudelft:cropxr:sequencing"])
    await _published(session, tenant, author, name="for-pheno", audience=PHENO)

    assert await _visible_names(session, author) == {"for-pheno"}
    assert await _visible_names(session, sibling) == set()


# --- every surface that lists a published specification ---------------------


async def _author_and_outsider(session: AsyncSession):
    """An author in cropxr with a collaboration-published spec, and a stranger."""
    tenant, author = await _person(session, "surf-author", [PHENO])
    await _published(session, tenant, author, name="for-cropxr", audience=CROPXR)
    await _published(session, tenant, author, name="for-all", audience=EVERYONE)
    return tenant, author


async def test_the_rest_api_neither_lists_nor_serves_it_to_an_outsider(
    session: AsyncSession, app_db
) -> None:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from metaseed_hub.api import api_router
    from metaseed_hub.auth import TokenUser, get_current_user
    from metaseed_hub.database import get_session

    await _author_and_outsider(session)
    sub = f"rest-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    outsider = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(outsider)
    await session.flush()
    await record_memberships(session, outsider.id, [OTHER])
    await session.commit()

    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(
        sub=sub, email=f"{sub}@example.org", name="O", roles=[]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/specs")
        fetched = await client.get("/api/specs/for-cropxr/1.0")

    names = {row["name"] for row in listed.json()}
    assert "for-all" in names
    assert "for-cropxr" not in names
    assert fetched.status_code == 404


async def test_an_agent_does_not_list_it_for_an_outsider(server, session: AsyncSession) -> None:
    from tests.mcp_helpers import _calling_with, _tool, _user_with_token

    await _author_and_outsider(session)
    _t, outsider, secret, _token = await _user_with_token(
        session, slug=f"mcp{uuid4().hex[:6]}", email=f"mcp{uuid4().hex[:6]}@example.org"
    )
    await record_memberships(session, outsider.id, [OTHER])
    await session.commit()

    list_profiles = await _tool(server, "list_profiles")
    with _calling_with(secret):
        listed = await list_profiles()

    # Asserted on the answer the agent actually receives, whatever shape the
    # tool wraps it in.
    assert "for-all" in listed
    assert "for-cropxr" not in listed


def _page(path: str, sub: str) -> str:
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient
    from starlette.routing import Mount

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.main import create_app
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    token = TokenUser(sub=sub, email=f"{sub}@example.org", name="O", roles=[])
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: token
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=token),
    ):
        response = TestClient(app).get(path)
    assert response.status_code == 200, response.status_code
    return response.text


@pytest.mark.parametrize("path", ["/hub/spec-builder", "/hub/datasets/new"])
async def test_no_page_offers_it_to_an_outsider(session: AsyncSession, app_db, path: str) -> None:
    """The specifications list and the profile picker."""
    await _author_and_outsider(session)
    sub = f"page-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    outsider = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(outsider)
    await session.flush()
    await record_memberships(session, outsider.id, [OTHER])
    await session.commit()

    html = _page(path, sub)

    assert "for-all" in html, f"{path} does not offer the hub-wide specification either"
    assert "for-cropxr" not in html


async def test_the_explorer_catalog_does_not_offer_it_to_an_outsider(
    session: AsyncSession,
) -> None:
    """The explorer page builds its catalog separately from the HTML."""
    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.explore_routes import _build_explore_catalog

    await _author_and_outsider(session)
    sub = f"cat-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    outsider = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(outsider)
    await session.flush()
    await record_memberships(session, outsider.id, [OTHER])
    await session.commit()

    profiles, _versions, names = await _build_explore_catalog(
        session, TokenUser(sub=sub, email=f"{sub}@example.org", name="O", roles=[])
    )

    offered = " ".join(profiles) + " " + " ".join(str(v) for v in names.values())
    assert "for-all" in offered
    assert "for-cropxr" not in offered


# --- choosing the audience from the interface ------------------------------


async def _draft_owned_by(session: AsyncSession, tenant, user, name: str):
    from metaseed_hub.models import SpecDraft
    from metaseed_hub.sharing import record_creator, resource_for

    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name=name,
        version="1.0",
        spec_data=_payload(name),
    )
    session.add(draft)
    await record_creator(session, resource_for("draft"), draft, user.id)
    await session.commit()
    return draft


async def test_the_editor_offers_the_publishers_collaborations(
    session: AsyncSession, app_db
) -> None:
    """Publishing cannot ask for an audience the person cannot choose from."""
    tenant, author = await _person(session, "editor-author", [PHENO])
    draft = await _draft_owned_by(session, tenant, author, "to-publish")

    html = _page(f"/hub/spec-builder/{draft.id}", author.keycloak_id)

    assert 'data-testid="publish-audience"' in html
    assert 'value=""' in html, "Everyone must be offered"
    assert f'value="{CROPXR}"' in html


async def test_publishing_to_a_collaboration_records_it(session: AsyncSession, app_db) -> None:
    from metaseed_hub.ui.spec_builder.publishing import audience_for_publisher

    tenant, author = await _person(session, "pub-author", [PHENO])

    chosen = await audience_for_publisher(session, author.id, CROPXR)
    everyone = await audience_for_publisher(session, author.id, "")

    assert chosen == CROPXR
    assert everyone is EVERYONE


async def test_publishing_to_a_collaboration_you_are_not_in_is_refused(
    session: AsyncSession, app_db
) -> None:
    """Otherwise a specification could be handed to a group one cannot see."""
    from metaseed_hub.collaborations import NotInCollaborationError
    from metaseed_hub.ui.spec_builder.publishing import audience_for_publisher

    _tenant, author = await _person(session, "pub-outsider", [OTHER])

    with pytest.raises(NotInCollaborationError):
        await audience_for_publisher(session, author.id, CROPXR)


async def test_the_specs_page_names_the_audience(session: AsyncSession, app_db) -> None:
    tenant, author = await _person(session, "audience-page", [PHENO])
    await _published(session, tenant, author, name="for-cropxr", audience=CROPXR)
    await _published(session, tenant, author, name="for-all", audience=EVERYONE)

    html = _page("/hub/spec-builder", author.keycloak_id)

    assert 'data-testid="audience-for-cropxr"' in html
    assert "cropxr" in html
    assert 'data-testid="audience-for-all"' in html
    assert "Everyone" in html
