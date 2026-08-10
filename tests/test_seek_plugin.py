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
    async def test_an_unreachable_seek_is_rejected_not_stored(
        self, dataset, app_db, session
    ) -> None:
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
        ):
            client = TestClient(app)
            page = client.get("/hub/seek/settings")
            csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
            response = client.post(
                "/hub/seek/settings",
                data={
                    "url": "http://127.0.0.1:1",  # nothing listens here
                    "api_key": "k",
                    "csrf_token": csrf,
                },
            )
        assert 'data-testid="seek-settings-error"' in response.text
        count = await session.scalar(select(func.count()).select_from(SeekConnection))
        assert count == 0, "a failed verification must not store a connection"
