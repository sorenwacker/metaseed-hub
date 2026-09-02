"""What the center pane shows on arrival, and what the sharing badge counts.

Rendered-page tests: the template is the contract. Reported twice on the same
day: opening a dataset showed only "select an entity" with history beneath,
and the Sharing tab said 1 for a dataset shared with nobody, because the
owner's own membership row was counted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.sharing import record_creator, resource_for
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state

_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[])


def _get(path: str) -> str:
    app = create_app()
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_TOKEN),
    ):
        response = TestClient(app).get(path)
    assert response.status_code == 200, response.status_code
    return response.text


def _overview(html: str) -> str:
    start = html.index('data-testid="entity-overview"')
    return html[start : html.index("</section>", start)]


async def test_an_empty_dataset_says_it_has_no_entities(ena_dataset, app_db) -> None:
    html = _get(f"/hub/datasets/{ena_dataset.id}")
    assert 'class="overview-tab active" data-tab="overview"' in html
    assert "No entities yet" in _overview(html)


async def test_the_overview_lists_every_entity_as_a_link_into_the_editor(
    ena_dataset, app_db, session: AsyncSession
) -> None:
    state = await ensure_dataset_facade(ena_dataset, session)
    study = state.add_node(
        "Study", {"alias": "study-1", "title": "Soil cores"}, skip_validation=True
    )
    for alias in ("s1", "s2"):
        state.add_node("Sample", {"alias": alias}, parent_id=study.id, skip_validation=True)
    await save_dataset_state(session, ena_dataset, state, _TOKEN)

    overview = _overview(_get(f"/hub/datasets/{ena_dataset.id}"))
    assert f'hx-get="/hub/datasets/{ena_dataset.id}/entity/{study.id}"' in overview
    assert 'hx-target="#editor"' in overview
    assert ">study-1</a>" in overview
    assert 'data-testid="entity-count-Study">1<' in overview
    assert 'data-testid="entity-count-Sample">2<' in overview


async def test_the_sharing_badge_leaves_the_viewer_out(
    ena_dataset, app_db, session: AsyncSession
) -> None:
    """The owner's own row is a membership like any other, and counting it
    made every dataset look shared with one person."""
    from sqlalchemy import select

    from metaseed_hub.models import User

    owner = (await session.execute(select(User).where(User.keycloak_id == "kc-1"))).scalar_one()
    await record_creator(session, resource_for("dataset"), ena_dataset, owner.id)
    await session.commit()

    panel = _get(f"/hub/sharing/dataset/{ena_dataset.id}/members")
    assert 'data-testid="sharing-count">0<' in panel


def _graph_container(html: str) -> str:
    return html[html.index('id="graph-container"') :]


async def test_the_graph_opens_beside_the_editor_rather_than_replacing_it(
    ena_dataset, app_db
) -> None:
    """Reported: the graph could not be shown at the same time as the entity
    table. The graph now lives on the dataset page, hidden until toggled, in
    the same center panes as the editor; the standalone page stays for a second
    window."""
    html = _get(f"/hub/datasets/{ena_dataset.id}")
    assert 'id="dataset-graph-btn"' in html
    assert 'id="graph-container"' in html and 'id="graph-view"' in html
    assert 'id="dataset-panes"' in html
    container = _graph_container(html)
    assert 'class="graph-container hidden"' in container, "closed until asked for"
    assert f'href="/hub/datasets/{ena_dataset.id}/graph"' in container, "a new-window link"
    assert 'id="editor"' in html
    # The drawing is the library's; the page supplies only the data URL.
    assert "/hub/static/js/graph.js" in html
    assert f"/hub/datasets/{ena_dataset.id}/api/graph" in html
