"""A dataset downloads as YAML, and the file imports back as the same dataset.

The hub read YAML and wrote only Excel, and its documentation listed a JSON
export that no route served. See docs/datasets/import-export.md, *YAML*.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi.testclient import TestClient
from metaseed import MetaseedClient

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.ui.helpers import add_entities_in_order, group_entities_by_type
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state
from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.services.export import export_to_yaml, generate_filename

_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[])
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"


def _trial() -> MetaseedClient:
    """An investigation holding a study, with a date and a URL among the values."""
    client = MetaseedClient("miappe", "1.2")
    investigation = client.create_entity(
        "Investigation",
        {
            "unique_id": "INV-1",
            "title": "Drought trial",
            "submission_date": "2024-03-01",
            "license": "https://creativecommons.org/licenses/by/4.0/",
        },
        skip_validation=True,
    )
    client.create_entity(
        "Study",
        {"unique_id": "ST-1", "title": "Field study", "start_date": "2024-03-02"},
        parent_id=investigation.id,
        skip_validation=True,
    )
    return client


def _without_node_ids(entities: list[dict]) -> list[dict]:
    return [{k: v for k, v in entity.items() if k != "_node_id"} for entity in entities]


def test_the_document_is_the_dataset_as_the_hub_holds_it() -> None:
    client = _trial()

    document = yaml.safe_load(export_to_yaml(client.facade))

    assert document == client.serialize()
    assert (document["profile"], document["version"]) == ("miappe", "1.2")
    assert [entity["_type"] for entity in document["entities"]] == ["Investigation", "Study"]


def test_the_file_import_rebuilds_the_same_entities_and_tree() -> None:
    """Through the two steps the New Dataset import takes with such a file."""
    exported = yaml.safe_load(export_to_yaml(_trial().facade))

    state = AppState(profile=exported["profile"], version=exported["version"])
    facade = state.get_or_create_facade()
    grouped = group_entities_by_type(exported["entities"], "Investigation")
    _, errors = add_entities_in_order(state, facade, grouped, "Investigation")

    assert not errors
    rebuilt = MetaseedClient.from_facade(facade).serialize()["entities"]
    assert _without_node_ids(rebuilt) == _without_node_ids(exported["entities"])


def test_the_file_name_takes_the_extension_asked_for() -> None:
    client = MetaseedClient("miappe", "1.2")

    assert generate_filename(client.facade, "yaml").endswith("-miappe-1-2-export.yaml")
    assert generate_filename(client.facade).endswith(".xlsx")


async def test_the_route_downloads_the_dataset_as_yaml(ena_dataset, app_db, session) -> None:
    state = await ensure_dataset_facade(ena_dataset, session)
    state.add_node("Study", {"alias": "study-1", "title": "Soil cores"}, skip_validation=True)
    await save_dataset_state(session, ena_dataset, state, _TOKEN)

    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_TOKEN),
    ):
        response = TestClient(create_app()).get(f"/hub/datasets/{ena_dataset.id}/export/yaml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.headers["content-disposition"].endswith('.yaml"')
    document = yaml.safe_load(response.text)
    assert document["profile"] == "ena"
    assert [entity["alias"] for entity in document["entities"]] == ["study-1"]


def test_the_dataset_page_offers_the_download() -> None:
    page = (TEMPLATES / "dataset.html").read_text()

    assert 'href="/hub/datasets/{{ dataset.id }}/export/yaml"' in page
    assert 'data-testid="btn-export-yaml"' in page
