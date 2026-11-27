"""Each user switches the repository integrations on for themselves (261126).

Until 261126 an integration was granted by an SRAM group named after it. The
hub no longer reads SRAM groups for this: every signed-in user may use every
integration, each is off until the user switches it on under *Integrations* on
their profile page, and the server refuses what is switched off.
See docs/administration.md, *Repository integrations*.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.features import FEATURES, feature_of_action, features_of, set_feature
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import get_current_user_from_cookie, tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from tests.factories import make_dataset, make_tenant, make_user

PREFIX = "urn:mace:surf.nl:sram:group:"
TOKEN = TokenUser(sub="kc-feat", email="feat@example.org", name="F", roles=[])


def test_an_action_belongs_to_its_adapter_s_feature() -> None:
    assert feature_of_action("ena-import") == "ena"
    assert feature_of_action("pride") == "pride"
    assert feature_of_action("brapi-import") == "brapi"
    assert feature_of_action("dcat") == "dcat"
    assert feature_of_action("nope") is None


@pytest.fixture
async def person(session: AsyncSession):
    """A signed-in person with ena switched on only, an empty ena dataset and a pride one."""
    tenant = make_tenant(slug=tenant_slug_for("kc-feat"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-feat", email="feat@example.org")
    session.add(user)
    await session.flush()
    await set_feature(session, user.id, "ena", enabled=True)
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
        assert "profile page" in refused.text

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

    async def test_switching_seek_on_shows_it(self, app_db, person) -> None:
        enabled = await _post("/hub/auth/profile/features", {"feature": "seek", "enabled": "on"})

        assert enabled.status_code in (200, 303)
        assert 'data-testid="nav-seek"' in (await _get("/hub/")).text
        assert (await _get("/hub/seek")).status_code == 200

    async def test_switching_ena_off_hides_it(self, app_db, person) -> None:
        await _post("/hub/auth/profile/features", {"feature": "ena"})

        page = (await _get("/hub/datasets/new")).text
        assert 'data-testid="btn-import-ena"' not in page

    async def test_an_unknown_feature_is_refused(self, app_db, person) -> None:
        refused = await _post("/hub/auth/profile/features", {"feature": "ftp", "enabled": "on"})

        assert refused.status_code == 400

    async def test_the_profile_page_lists_every_integration_with_its_state(
        self, app_db, person
    ) -> None:
        page = (await _get("/hub/auth/profile")).text

        for feature in FEATURES:
            assert f'data-testid="feature-{feature}"' in page
        assert 'data-testid="feature-ena-on"' in page
        assert 'data-testid="feature-pride-off"' in page


@pytest.mark.asyncio
class TestSramGroupsPlayNoPart:
    async def test_a_group_named_after_a_feature_grants_nothing(
        self, app_db, session: AsyncSession
    ) -> None:
        tenant = make_tenant(slug="t-groups")
        session.add(tenant)
        await session.flush()
        user = make_user(tenant=tenant, keycloak_id="kc-groups", email="groups@example.org")
        session.add(user)
        await session.flush()
        await record_memberships(
            session, user.id, [PREFIX + "tudelft:cropxr:ena", PREFIX + "tudelft:x:metaseed-seek"]
        )
        await session.commit()

        assert await features_of(session, user.id) == set()

    async def test_switching_off_and_on_round_trips(self, app_db, person, session) -> None:
        user, _, _ = person
        await set_feature(session, user.id, "pride", enabled=True)
        await set_feature(session, user.id, "ena", enabled=False)
        await session.commit()

        assert await features_of(session, user.id) == {"pride"}


@pytest.mark.asyncio
class TestSwitchingWithoutAReload:
    """A switch reloaded the whole profile page and threw the view back to the
    top; from the page it is an htmx request now, answered with the section
    alone (261127)."""

    async def _switch(self, data: dict[str, str]) -> httpx.Response:
        with _signed_in():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app()), base_url="https://test"
            ) as client:
                page = await client.get("/hub/datasets/new")
                csrf = page.cookies[CSRF_TOKEN_COOKIE]
                return await client.post(
                    "/hub/auth/profile/features",
                    data=data,
                    cookies=page.cookies,
                    headers={"X-CSRF-Token": csrf, "Origin": "https://test", "HX-Request": "true"},
                )

    async def test_an_htmx_switch_answers_with_the_section_alone(self, app_db, person) -> None:
        response = await self._switch({"feature": "seek", "enabled": "on"})

        assert response.status_code == 200
        assert 'id="integrations"' in response.text
        assert 'data-testid="feature-seek-on"' in response.text
        assert "<html" not in response.text.lower()

    async def test_a_switch_says_to_reload_open_pages(self, app_db, person) -> None:
        response = await self._switch({"feature": "seek", "enabled": "on"})

        notice = response.text[response.text.index('data-testid="integrations-saved"') :]
        assert "FAIRDOM-SEEK switched on" in notice[:300]
        assert "reload" in notice[:300].lower()

    async def test_the_page_itself_shows_no_notice(self, app_db, person) -> None:
        assert 'data-testid="integrations-saved"' not in (await _get("/hub/auth/profile")).text

    async def test_the_profile_page_switches_by_htmx(self, app_db, person) -> None:
        page = (await _get("/hub/auth/profile")).text
        section = page[page.index('id="integrations"') :]

        assert 'hx-post="/hub/auth/profile/features"' in section
        assert 'hx-target="#integrations"' in section
        assert "this.form.submit()" not in section
