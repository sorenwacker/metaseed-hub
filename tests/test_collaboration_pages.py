"""The collaboration features as the pages show them.

Sign-in records the snapshot, the profile shows it, People lists who is in a
collaboration, and the sharing panel offers a collaboration beside a person.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Mount

from metaseed_hub.auth import TokenUser
from metaseed_hub.collaborations import entitled_urns_of, record_memberships
from metaseed_hub.main import create_app
from metaseed_hub.sharing import Role, add_grant, record_creator, resource_for
from metaseed_hub.ui.dependencies import (
    ensure_tenant_and_user,
    get_current_user_from_cookie,
    tenant_slug_for,
)
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.auth import _after_sign_in
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
CROPXR = PREFIX + "tudelft:cropxr"
_CSRF = get_or_create_csrf_token(Mock(cookies={}))
_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[PHENO])


def _client(token: TokenUser = _TOKEN) -> TestClient:
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: token
    client = TestClient(app)
    client.cookies.set(CSRF_TOKEN_COOKIE, _CSRF)
    return client


def _get(path: str, token: TokenUser = _TOKEN) -> str:
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=token),
    ):
        response = _client(token).get(path)
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


async def _signed_in_people(session: AsyncSession):
    """The page user (kc-1) and a colleague, both in cropxr, plus a stranger."""
    me_tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(me_tenant)
    await session.flush()
    me = make_user(tenant=me_tenant, keycloak_id="kc-1", email="u@example.org", display_name="U")
    session.add(me)
    other_tenant = make_tenant(slug="colleague")
    session.add(other_tenant)
    await session.flush()
    colleague = make_user(
        tenant=other_tenant, keycloak_id="kc-2", email="c@example.org", display_name="Colleague"
    )
    session.add(colleague)
    await session.flush()
    await record_memberships(session, me.id, [PHENO])
    await record_memberships(session, colleague.id, [PHENO])
    await session.commit()
    return me_tenant, me, colleague


async def test_sign_in_records_the_snapshot(session: AsyncSession) -> None:
    token = TokenUser(sub="kc-new", email="n@example.org", name="N", roles=[], entitlements=[PHENO])

    await _after_sign_in(session, token)

    _, user = await ensure_tenant_and_user(session, token)
    assert await entitled_urns_of(session, user.id) == {PHENO, CROPXR}


async def test_the_profile_lists_your_collaborations(session: AsyncSession, app_db) -> None:
    await _signed_in_people(session)

    html = _get("/hub/auth/profile")

    assert "Your collaborations" in html
    assert 'data-testid="collaboration-tudelft:cropxr"' in html
    assert "phenotyping" in html


async def test_people_lists_the_signed_in_members_of_each_collaboration(
    session: AsyncSession, app_db
) -> None:
    await _signed_in_people(session)

    html = _get("/hub/people")

    assert 'data-testid="collaboration-tudelft:cropxr"' in html
    assert "c@example.org" in html
    assert "Colleague" in html


async def test_people_says_so_when_you_are_in_no_collaboration(
    session: AsyncSession, app_db
) -> None:
    """A reading was taken and named nothing, which is not the same as no
    reading having been taken; see test_membership_is_recorded_when_asked."""
    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, [])
    await session.commit()

    html = _get("/hub/people")

    assert 'data-testid="no-collaborations"' in html


async def test_the_header_links_to_people(session: AsyncSession, app_db) -> None:
    await _signed_in_people(session)
    assert 'href="/hub/people"' in _get("/hub/")


async def test_the_sharing_panel_offers_collaborations_and_suggests_people(
    session: AsyncSession, app_db
) -> None:
    tenant, me, _ = await _signed_in_people(session)
    dataset = make_dataset(tenant=tenant, profile="ena", version="1.0")
    session.add(dataset)
    await record_creator(session, resource_for("dataset"), dataset, me.id)
    await session.commit()

    html = _get(f"/hub/sharing/dataset/{dataset.id}/members")

    assert 'data-testid="grant-form"' in html
    assert f'<option value="{CROPXR}"' in html
    assert 'list="people-suggestions"' in html
    assert '<option value="c@example.org"' in html


async def test_an_owner_can_grant_and_the_badge_counts_it(session: AsyncSession, app_db) -> None:
    tenant, me, _ = await _signed_in_people(session)
    dataset = make_dataset(tenant=tenant, profile="ena", version="1.0")
    session.add(dataset)
    await record_creator(session, resource_for("dataset"), dataset, me.id)
    await session.commit()

    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_TOKEN),
    ):
        response = _client().post(
            f"/hub/sharing/dataset/{dataset.id}/collaborations",
            data={"urn": CROPXR, "role": "editor", "csrf_token": _CSRF},
        )
    assert response.status_code == 200, response.text[:300]
    assert 'data-testid="grant-tudelft:cropxr"' in response.text
    assert 'data-testid="sharing-count"' in response.text
    assert ">1</span>" in response.text


async def test_a_dataset_granted_to_your_collaboration_is_on_your_list(
    session: AsyncSession, app_db
) -> None:
    _, _, colleague = await _signed_in_people(session)
    theirs = make_dataset(tenant=await _tenant_of(session, colleague), profile="ena", version="1.0")
    theirs.name = "granted-to-cropxr"
    session.add(theirs)
    await record_creator(session, resource_for("dataset"), theirs, colleague.id)
    await session.commit()
    await add_grant(
        session,
        resource_for("dataset"),
        theirs.id,
        actor_id=colleague.id,
        urn=CROPXR,
        role=Role.VIEWER,
    )

    html = _get("/hub/")

    assert f'href="/hub/datasets/{theirs.id}"' in html
    assert "granted-to-cropxr" in html


async def _tenant_of(session: AsyncSession, user):
    from metaseed_hub.models import Tenant

    return await session.get(Tenant, user.tenant_id)


async def test_a_granted_dataset_card_names_the_collaboration(
    session: AsyncSession, app_db
) -> None:
    """Without it a colleague's dataset appears in your list unexplained."""
    _, _, colleague = await _signed_in_people(session)
    theirs = make_dataset(tenant=await _tenant_of(session, colleague), profile="ena", version="1.0")
    theirs.name = "granted-card"
    session.add(theirs)
    await record_creator(session, resource_for("dataset"), theirs, colleague.id)
    await session.commit()
    await add_grant(
        session,
        resource_for("dataset"),
        theirs.id,
        actor_id=colleague.id,
        urn=CROPXR,
        role=Role.VIEWER,
    )

    html = _get("/hub/")

    card = html[html.index(f'href="/hub/datasets/{theirs.id}"') :]
    card = card[: card.index("</a>")]
    assert 'data-testid="granted-by"' in card
    assert "cropxr" in card


async def test_a_granted_draft_card_names_the_collaboration(session: AsyncSession, app_db) -> None:
    _, _, colleague = await _signed_in_people(session)
    from metaseed_hub.models import SpecDraft

    draft = SpecDraft(
        tenant_id=colleague.tenant_id,
        user_id=colleague.id,
        name="granted-draft",
        version="1.0",
        spec_data={
            "spec": {
                "name": "granted-draft",
                "version": "1.0",
                "root_entity": "Sample",
                "entities": {"Sample": {"description": "a sample", "fields": []}},
            }
        },
    )
    session.add(draft)
    await record_creator(session, resource_for("draft"), draft, colleague.id)
    await session.commit()
    await add_grant(
        session,
        resource_for("draft"),
        draft.id,
        actor_id=colleague.id,
        urn=CROPXR,
        role=Role.VIEWER,
    )

    html = _get("/hub/spec-builder")

    assert 'data-testid="granted-by"' in html
    assert "cropxr" in html
