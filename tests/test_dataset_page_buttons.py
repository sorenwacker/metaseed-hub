"""Every adapter export the registry offers for the profile is a button.

Rendered-page tests, not unit tests of the menu builder: what the template
actually receives and draws is the contract. Adapters are plugins available
to every signed-in user — the per-group FeatureGrant filter that used to
decide these buttons hid ALL of them, because nothing ever wrote a grant.
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


def _page(dataset_id: str) -> str:
    app = create_app()
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_user()),
    ):
        client = TestClient(app)
        response = client.get(f"/hub/datasets/{dataset_id}")
    assert response.status_code == 200, response.status_code
    return response.text


async def test_the_profiles_registry_exports_are_all_buttons(ena_dataset, app_db) -> None:
    html = _page(ena_dataset.id)
    assert 'data-testid="btn-export-ena"' in html
    assert 'data-testid="btn-export-dcat"' in html


async def test_another_profiles_exporter_is_not_offered(ena_dataset, app_db) -> None:
    # The profile gate (the registry's) still holds: an ena dataset is not
    # offered the PRIDE exporter.
    html = _page(ena_dataset.id)
    assert 'data-testid="btn-export-pride"' not in html


async def test_the_source_import_shows_progress_and_blocks_a_second_click(
    ena_dataset, app_db
) -> None:
    """Reported: no sign the ENA import was running, so the button got clicked
    again. htmx toggles the indicator for the request's duration and disables
    the button, both declared on the form."""
    html = _page(ena_dataset.id)
    assert 'hx-indicator="#import-source-progress"' in html
    assert 'id="import-source-progress"' in html
    assert 'data-testid="form-import-source"' in html
    form_start = html.index('data-testid="form-import-source"')
    form = html[html.rfind("<form", 0, form_start) : html.index("</form>", form_start)]
    assert "hx-disabled-elt" in form
