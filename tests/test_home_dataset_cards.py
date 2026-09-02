"""Each dataset card on the home page says how many entities the dataset holds.

Asked for after the cards showed only profile and date: with a screen of
similar names there was no telling an empty dataset from a filled one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Mount

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import get_current_user_from_cookie
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state

_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[])


def _home() -> str:
    # The home route binds the cookie dependency at import time, so it is
    # overridden rather than patched by name, and on the hub sub-application
    # mounted at /hub, which keeps its own override table.
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: _TOKEN
    response = TestClient(app).get("/hub/")
    assert response.status_code == 200, response.status_code
    return response.text


def _card(html: str, dataset_id: str) -> str:
    start = html.index(f'href="/hub/datasets/{dataset_id}"')
    return html[start : html.index("</a>", start)]


async def test_an_empty_dataset_says_so_on_its_card(ena_dataset, app_db) -> None:
    card = _card(_home(), ena_dataset.id)
    assert 'data-testid="entity-count"' in card
    assert "No entities" in card


async def test_a_card_counts_entities_and_names_the_types_on_hover(
    ena_dataset, app_db, session: AsyncSession
) -> None:
    state = await ensure_dataset_facade(ena_dataset, session)
    study = state.add_node("Study", {"alias": "study-1"}, skip_validation=True)
    for alias in ("s1", "s2"):
        state.add_node("Sample", {"alias": alias}, parent_id=study.id, skip_validation=True)
    await save_dataset_state(session, ena_dataset, state, _TOKEN)

    card = _card(_home(), ena_dataset.id)
    assert ">3 entities<" in card
    assert 'title="1 Study, 2 Sample"' in card
