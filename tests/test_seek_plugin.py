"""The hub SEEK plugin, gated behind the seek feature.

The rendering standard this repo learned the hard way: assert the page a user
actually gets, not only the helpers behind it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.main import create_app
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_tenant, make_user


def _user() -> TokenUser:
    return TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])


@pytest.fixture
async def app_db(session):
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


@pytest.fixture
async def dataset(session: AsyncSession):
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    ds = make_dataset(tenant=tenant, profile="seek-ready-template", version="3.0")
    session.add(ds)
    await session.commit()
    return ds


def _get(path: str, features: set[str]) -> TestClient.Response:
    app = create_app()
    with (
        patch(
            "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
            AsyncMock(return_value=_user()),
        ),
        patch(
            "metaseed_hub.ui.routes.dataset.editor.user_feature_set",
            AsyncMock(return_value=features),
        ),
        patch(
            "metaseed_hub.features.enabled_features",
            AsyncMock(return_value=features),
        ),
    ):
        return TestClient(app).get(path)


class TestCrypto:
    def test_a_secret_round_trips(self) -> None:
        assert decrypt_secret(encrypt_secret("s3cr3t")) == "s3cr3t"

    def test_garbage_is_none_not_an_exception(self) -> None:
        assert decrypt_secret("not-a-token") is None


class TestThePanelIsGated:
    async def test_the_seek_group_sees_the_panel(self, dataset, app_db) -> None:
        html = _get(f"/hub/datasets/{dataset.id}", {"seek"}).text
        assert 'data-testid="seek-panel"' in html
        assert 'data-testid="btn-seek-push"' in html

    async def test_without_the_grant_the_panel_does_not_exist(self, dataset, app_db) -> None:
        html = _get(f"/hub/datasets/{dataset.id}", set()).text
        assert 'data-testid="seek-panel"' not in html


class TestTheRoutesAreGated:
    async def test_settings_is_404_without_the_feature(self, dataset, app_db) -> None:
        response = _get("/hub/seek/settings", set())
        assert response.status_code == 404

    async def test_settings_renders_with_the_feature(self, dataset, app_db) -> None:
        response = _get("/hub/seek/settings", {"seek"})
        assert response.status_code == 200
        assert 'data-testid="seek-api-key"' in response.text

    async def test_the_api_key_is_never_rendered(self, dataset, app_db) -> None:
        # Even a configured connection shows only its URL.
        response = _get("/hub/seek/settings", {"seek"})
        assert "api_key_encrypted" not in response.text


class TestSettingsSave:
    async def test_an_unreachable_seek_is_stored_with_its_error(
        self, dataset, app_db, session
    ) -> None:
        """Nothing typed is thrown away — a failed check records why instead."""
        import httpx as _httpx
        from sqlalchemy import select

        from metaseed_hub.models import SeekConnection

        app = create_app()
        with (
            patch(
                "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
                AsyncMock(return_value=_user()),
            ),
            patch(
                "metaseed_hub.features.enabled_features",
                AsyncMock(return_value={"seek"}),
            ),
        ):
            async with _httpx.AsyncClient(
                transport=_httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get("/hub/seek/settings")
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    "/hub/seek/settings",
                    data={
                        "url": "http://127.0.0.1:1",  # nothing listens here
                        "api_key": "k",
                        "csrf_token": csrf,
                    },
                    cookies=page.cookies,
                )
        assert 'data-testid="seek-settings-error"' in response.text
        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.verified_at is None
        assert stored.last_error


class TestVerificationSaysWhatFailed:
    """The first production attempt failed on 'no projects' and read as a bad
    key. Each cause now has its own message, and a working connection is no
    longer rejected for being project-less."""

    def test_a_name_that_does_not_resolve_says_so(self) -> None:
        import socket

        from metaseed_hub.ui.routes.seek import _verification_failure

        message = _verification_failure(
            socket.gaierror("[Errno -3] Temporary failure in name resolution"),
            "https://seek.local:3000",
        )
        assert "cannot resolve seek.local:3000" in message

    def test_a_refused_connection_is_not_blamed_on_the_key(self) -> None:
        import httpx

        from metaseed_hub.ui.routes.seek import _verification_failure

        message = _verification_failure(httpx.ConnectError("refused"), "https://seek.example.org")
        assert "Nothing answered" in message
        assert "key" not in message.lower()

    def test_a_rejected_key_says_the_key(self) -> None:
        import httpx

        from metaseed_hub.ui.routes.seek import _verification_failure

        exc = httpx.HTTPStatusError(
            "401",
            request=httpx.Request("GET", "https://seek.example.org/projects"),
            response=httpx.Response(401),
        )
        message = _verification_failure(exc, "https://seek.example.org")
        assert "rejected the API key" in message

    async def _post_settings(self, url: str, factory_effect, session):
        """POST the settings form through ASGITransport (this path writes, and
        TestClient's own event loop cannot share the fixture's pool)."""
        import httpx as _httpx

        app = create_app()
        with (
            patch(
                "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
                AsyncMock(return_value=_user()),
            ),
            patch(
                "metaseed_hub.features.enabled_features",
                AsyncMock(return_value={"seek"}),
            ),
            patch("metaseed.seek.client_from_settings") as factory,
        ):
            factory_effect(factory)
            async with _httpx.AsyncClient(
                transport=_httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get("/hub/seek/settings")
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                return await client.post(
                    "/hub/seek/settings",
                    data={"url": url, "api_key": "k", "csrf_token": csrf},
                    cookies=page.cookies,
                )

    async def test_a_projectless_seek_is_saved_with_a_warning(
        self, dataset, app_db, session
    ) -> None:
        """Reaching SEEK with a valid key is a working connection; having no
        project is a thing to fix in SEEK, not a reason to refuse the key.

        Driven through ASGITransport rather than TestClient: this request
        writes, and TestClient's own event loop cannot share the fixture's
        connection pool.
        """
        import httpx as _httpx
        from sqlalchemy import func, select

        from metaseed_hub.models import SeekConnection

        app = create_app()
        with (
            patch(
                "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
                AsyncMock(return_value=_user()),
            ),
            patch(
                "metaseed_hub.features.enabled_features",
                AsyncMock(return_value={"seek"}),
            ),
            patch("metaseed.seek.client_from_settings") as factory,
        ):
            factory.return_value.list_projects.return_value = []
            transport = _httpx.ASGITransport(app=app)
            async with _httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                page = await client.get("/hub/seek/settings")
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    "/hub/seek/settings",
                    data={
                        "url": "https://seek.example.org",
                        "api_key": "k",
                        "csrf_token": csrf,
                    },
                    cookies=page.cookies,
                )
        assert 'data-testid="seek-settings-ok"' in response.text
        assert "no project" in response.text
        count = await session.scalar(select(func.count()).select_from(SeekConnection))
        assert count == 1, "a reachable SEEK with a valid key must be stored"


class TestAFailedCheckKeepsWhatWasTyped(TestVerificationSaysWhatFailed):
    """Losing the settings to a failed check meant retyping the API key to fix
    a typo in the URL, and losing a good key to a SEEK that was briefly down."""

    async def test_a_failed_check_still_stores_the_connection(
        self, dataset, app_db, session
    ) -> None:
        import httpx as _httpx
        from sqlalchemy import select

        from metaseed_hub.models import SeekConnection

        def unreachable(factory):
            factory.return_value.list_projects.side_effect = _httpx.ConnectError("no")

        response = await self._post_settings("https://seek.example.org", unreachable, session)
        assert 'data-testid="seek-settings-error"' in response.text

        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.url == "https://seek.example.org"
        assert stored.api_key_encrypted, "the key must survive a failed check"
        assert stored.verified_at is None
        assert "Nothing answered" in (stored.last_error or "")

    async def test_the_form_still_shows_the_url_after_a_failure(
        self, dataset, app_db, session
    ) -> None:
        import httpx as _httpx

        def unreachable(factory):
            factory.return_value.list_projects.side_effect = _httpx.ConnectError("no")

        response = await self._post_settings("https://seek.example.org", unreachable, session)
        assert "https://seek.example.org" in response.text

    async def test_the_status_says_working_or_not(self, dataset, app_db, session) -> None:
        def working(factory):
            factory.return_value.list_projects.return_value = [("1", "Tulip")]

        ok = await self._post_settings("https://seek.example.org", working, session)
        assert 'data-testid="seek-status-ok"' in ok.text

        def broken(factory):
            factory.return_value.list_projects.side_effect = OSError("down")

        bad = await self._post_settings("https://seek.example.org", broken, session)
        assert 'data-testid="seek-status-bad"' in bad.text

    async def test_the_dataset_panel_shows_the_status_too(self, dataset, app_db, session) -> None:
        def working(factory):
            factory.return_value.list_projects.return_value = [("1", "Tulip")]

        import httpx as _httpx

        await self._post_settings("https://seek.example.org", working, session)
        app = create_app()
        with (
            patch(
                "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
                AsyncMock(return_value=_user()),
            ),
            patch(
                "metaseed_hub.ui.routes.dataset.editor.user_feature_set",
                AsyncMock(return_value={"seek"}),
            ),
        ):
            async with _httpx.AsyncClient(
                transport=_httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                html = (await client.get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="seek-status-ok"' in html
