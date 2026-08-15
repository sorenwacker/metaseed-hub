"""A dataset's DCAT metadata is harvestable, not only downloadable (#30 upstream).

FAIR assessors (F-UJI et al.) score from what a landing page exposes:
embedded JSON-LD, content negotiation, and typed links. A standalone export
file is never harvested. The dataset page therefore embeds the DCAT JSON-LD,
the dataset URL answers content negotiation (Accept: application/ld+json or
text/turtle), and responses carry a rel="describedby" Link. Reachability
still follows the deployment's authentication — harvesting needs whatever
visibility the operator grants — but the metadata is exposed the way
harvesters read it.
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
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


def _user() -> TokenUser:
    return TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])


@pytest.fixture
async def dataset(session: AsyncSession):
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    ds = make_dataset(tenant=tenant, name="harvest-me", profile="miappe", version="1.2")
    ds.data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {
                    "unique_id": "INV-1",
                    "title": "Wheat drought trial",
                    "description": "A field trial under drought stress.",
                },
                "children": [],
            }
        ],
    }
    session.add(ds)
    await session.commit()
    return ds


def _get(path: str, accept: str | None = None):
    app = create_app()
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_user()),
    ):
        client = TestClient(app)
        headers = {"Accept": accept} if accept else {}
        return client.get(path, headers=headers)


async def test_the_landing_page_embeds_json_ld(dataset, app_db) -> None:
    response = _get(f"/hub/datasets/{dataset.id}")
    assert response.status_code == 200
    assert 'type="application/ld+json"' in response.text
    assert "Wheat drought trial" in response.text


async def test_the_dataset_url_negotiates_json_ld(dataset, app_db) -> None:
    response = _get(f"/hub/datasets/{dataset.id}", accept="application/ld+json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/ld+json")
    assert "Wheat drought trial" in response.text


async def test_the_dataset_url_negotiates_turtle(dataset, app_db) -> None:
    response = _get(f"/hub/datasets/{dataset.id}", accept="text/turtle")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/turtle")
    assert "dcat" in response.text


async def test_responses_carry_a_describedby_link(dataset, app_db) -> None:
    response = _get(f"/hub/datasets/{dataset.id}")
    assert 'rel="describedby"' in response.headers.get("link", "")


async def test_dataset_text_cannot_close_the_embedded_script(
    dataset, session: AsyncSession, app_db
) -> None:
    """The card is embedded unescaped, so `</script>` in a title would end the
    block and everything after it would be parsed as HTML: stored XSS against
    anyone the dataset is shared with. `<` must not survive into the page."""
    from metaseed_hub.models import Tenant

    tenant = await session.get(Tenant, dataset.tenant_id)
    hostile = make_dataset(tenant=tenant, name="hostile", profile="miappe", version="1.2")
    hostile.data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {
                    "unique_id": "INV-1",
                    "title": "</script><img src=x onerror=alert(1)>",
                    "description": "breaking out",
                },
                "children": [],
            }
        ],
    }
    session.add(hostile)
    await session.commit()

    response = _get(f"/hub/datasets/{hostile.id}")

    assert response.status_code == 200
    embedded = response.text.split('type="application/ld+json">', 1)[1]
    assert "\\u003c" in embedded.split("</script>", 1)[0], (
        "the card must escape < before it is embedded"
    )
    assert "<img src=x" not in response.text

    # Escaping must not cost harvestability: the block is still JSON-LD, and
    # the title still reads as the text the user typed.
    import json

    card = json.loads(embedded.split("</script>", 1)[0])
    assert card["dct:title"] == "</script><img src=x onerror=alert(1)>"
