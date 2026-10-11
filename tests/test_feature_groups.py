"""Repository integrations are enabled per SRAM group, named after the feature.

Since 0.57.0 every signed-in user had every importer and exporter: a hub
table of grants sat in the way once, nothing wrote to it, and it was taken
out, while the landing page kept promising integrations rolled out per group.
The groups are SRAM's now: a person in a group named ena, pride, brapi,
metabolights, seek or dcat, in any of their collaborations, may use that
integration; nobody else sees it, and the server refuses it.
See docs/administration.md, *Features and SRAM groups*.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.features import feature_of_action, features_in
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import get_current_user_from_cookie, tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from tests.factories import make_dataset, make_tenant, make_user

PREFIX = "urn:mace:surf.nl:sram:group:"
TOKEN = TokenUser(sub="kc-feat", email="feat@example.org", name="F", roles=[])


class TestTheRule:
    def test_a_group_named_after_a_feature_grants_it_in_any_collaboration(self) -> None:
        urns = [
            PREFIX + "tudelft:cropxr",
            PREFIX + "tudelft:cropxr:ena",
            PREFIX + "wur:breeding:brapi",
        ]

        assert features_in(urns) == {"ena", "brapi"}

    def test_other_groups_and_the_collaboration_itself_grant_nothing(self) -> None:
        assert (
            features_in([PREFIX + "tudelft:metaseed", PREFIX + "tudelft:metaseed:admin"]) == set()
        )
        assert features_in([PREFIX + "tudelft:metaseed:reviewers"]) == set()

    def test_an_action_belongs_to_its_adapter_s_feature(self) -> None:
        assert feature_of_action("ena-import") == "ena"
        assert feature_of_action("pride") == "pride"
        assert feature_of_action("brapi-import") == "brapi"
        assert feature_of_action("dcat") == "dcat"
        assert feature_of_action("nope") is None


@pytest.fixture
async def person(session: AsyncSession):
    """A signed-in person in the ena group only, with an empty ena dataset and a pride one."""
    tenant = make_tenant(slug=tenant_slug_for("kc-feat"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-feat", email="feat@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, [PREFIX + "tudelft:cropxr:ena"])
    ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
    pride = make_dataset(tenant=tenant, profile="pride", version="2.0", name="test-pride")
    session.add_all([ena, pride])
    await session.commit()
    return user, ena, pride


def _app():
    from starlette.routing import Mount

    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: TOKEN
    return app


def _signed_in():
    return patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=TOKEN),
    )


async def _get(path: str) -> httpx.Response:
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="https://test"
        ) as client:
            return await client.get(path)


async def _post(path: str, data: dict[str, str]) -> httpx.Response:
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="https://test"
        ) as client:
            page = await client.get("/hub/datasets/new")
            csrf = page.cookies[CSRF_TOKEN_COOKIE]
            return await client.post(
                path,
                data=data,
                cookies=page.cookies,
                headers={"X-CSRF-Token": csrf, "Origin": "https://test"},
            )


@pytest.mark.asyncio
class TestWhatThePersonSees:
    async def test_the_new_dataset_tab_offers_their_repositories_only(self, app_db, person) -> None:
        page = (await _get("/hub/datasets/new")).text

        assert 'data-testid="btn-import-ena"' in page
        assert 'data-testid="btn-import-pride"' not in page
        assert 'data-testid="btn-import-miappe"' not in page

    async def test_a_job_for_another_repository_is_refused(self, app_db, person) -> None:
        refused = await _post("/hub/import/jobs", {"profile": "pride", "identifiers": "PXD000001"})

        assert 'data-testid="import-problem"' in refused.text
        assert "enabled per SRAM group" in refused.text

    async def test_their_dataset_offers_its_import_and_export(self, app_db, person) -> None:
        _, ena, _ = person

        page = (await _get(f"/hub/datasets/{ena.id}")).text

        assert 'data-testid="btn-import-source"' in page
        assert 'data-testid="btn-export-ena"' in page

    async def test_another_feature_s_dataset_offers_neither(self, app_db, person) -> None:
        _, _, pride = person

        page = (await _get(f"/hub/datasets/{pride.id}")).text

        assert 'data-testid="btn-import-source"' not in page
        assert 'data-testid="btn-export-pride"' not in page

    async def test_the_server_refuses_what_the_page_withholds(self, app_db, person) -> None:
        _, _, pride = person

        export = await _get(f"/hub/datasets/{pride.id}/export/adapter/pride")
        imported = await _post(f"/hub/datasets/{pride.id}/import-source", {"value": "PXD000001"})

        assert export.status_code == 403
        assert imported.status_code == 403

    async def test_seek_is_absent_from_the_header_and_refused(self, app_db, person) -> None:
        home = (await _get("/hub/")).text

        assert 'data-testid="nav-seek"' not in home
        assert (await _get("/hub/seek")).status_code == 403

    async def test_the_seek_group_shows_it(self, app_db, person, session: AsyncSession) -> None:
        user, _, _ = person
        await record_memberships(
            session, user.id, [PREFIX + "tudelft:cropxr:ena", PREFIX + "tudelft:metaseed:seek"]
        )
        await session.commit()

        assert 'data-testid="nav-seek"' in (await _get("/hub/")).text
        assert (await _get("/hub/seek")).status_code == 200
