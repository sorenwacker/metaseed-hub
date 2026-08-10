"""The export buttons a user sees must match the features their groups grant.

Rendered-page tests, not unit tests of the filter: the filter was correct while
the page showed every button to everyone, because nothing asserted what the
template actually receives and draws.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_tenant, make_user


@pytest.fixture
async def app_db(session):
    """The app-wide connection the routes open their own sessions from."""
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


def _user() -> TokenUser:
    return TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])


@pytest.fixture
async def ena_dataset(session: AsyncSession):
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    dataset = make_dataset(tenant=tenant, profile="ena", version="1.0")
    session.add(dataset)
    await session.commit()
    return dataset


def _page(dataset_id: str, features: set[str]) -> str:
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
    ):
        client = TestClient(app)
        response = client.get(f"/hub/datasets/{dataset_id}")
    assert response.status_code == 200, response.status_code
    return response.text


async def test_a_member_of_the_ena_group_sees_the_ena_export_button(ena_dataset, app_db) -> None:
    html = _page(ena_dataset.id, {"ena"})
    assert 'data-testid="btn-export-ena"' in html


async def test_without_the_group_the_button_does_not_exist(ena_dataset, app_db) -> None:
    html = _page(ena_dataset.id, set())
    assert 'data-testid="btn-export-' not in html


async def test_a_grant_shows_its_own_button_not_anothers(ena_dataset, app_db) -> None:
    html = _page(ena_dataset.id, {"dcat"})
    assert 'data-testid="btn-export-dcat"' in html
    assert 'data-testid="btn-export-ena"' not in html
