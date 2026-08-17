"""A cell edit either lands where it was addressed or it is refused.

The primitive-list handlers took `idx` straight off the URL and guarded it
with `idx < len(current_list)`. That is true for every negative number, so
`-1` wrote to, or deleted, the LAST item — an item the caller never named.
An out-of-range positive index took the other branch: nothing was written,
yet the response was 200 and the dataset was re-saved, so the client had no
way to learn the edit never happened.

The same handlers, plus the single-entity ones, passed `field_name` from the
URL into `EntityHelper.field_info`, which raises KeyError for a name the
entity does not have — a 500 where 400 is the honest answer. `update_table_cell`
already guarded exactly this; its siblings had not.

Both are now refusals: an address the dataset cannot resolve gets a 400, and
nothing is saved.
"""

from __future__ import annotations

from typing import Any

import pytest
from metaseed import MetaseedClient

from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.routes import table as table_routes


class _Request:
    """A form-posting request carrying one field value."""

    def __init__(self, value: str = "new") -> None:
        self._value = value

    async def form(self) -> dict[str, str]:
        return {"value": self._value}


@pytest.fixture
def investigation_with_publications() -> tuple[AppState, str]:
    """An Investigation holding a two-item primitive list."""
    client = MetaseedClient("miappe", "1.1")
    client.create_entity(
        "Investigation",
        {
            "unique_id": "INV-1",
            "title": "T",
            "associated_publications": ["doi:first", "doi:second"],
        },
    )

    state = AppState()
    state.profile = "miappe"
    state.version = "1.1"
    state.facade = client.facade
    state.invalidate_cache()
    node_id = next(iter(state.nodes_by_id))
    return state, node_id


def _publications(state: AppState, node_id: str) -> list[str]:
    return state.nodes_by_id[node_id].instance.model_dump()["associated_publications"]


async def _never_saves(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("a refused edit must not save the dataset")


@pytest.mark.asyncio
async def test_a_negative_index_does_not_edit_the_last_item(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.update_primitive_list_item(
        request=_Request("smuggled"),
        dataset_id="d-1",
        node_id=node_id,
        field_name="associated_publications",
        idx=-1,
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400
    assert _publications(state, node_id) == ["doi:first", "doi:second"]


@pytest.mark.asyncio
async def test_a_negative_index_does_not_delete_the_last_item(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.delete_primitive_list_item(
        request=_Request(),
        dataset_id="d-1",
        node_id=node_id,
        field_name="associated_publications",
        idx=-1,
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400
    assert _publications(state, node_id) == ["doi:first", "doi:second"]


@pytest.mark.asyncio
async def test_an_index_past_the_end_is_refused_not_silently_ignored(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 for an edit that never landed tells the client a lie."""
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.update_primitive_list_item(
        request=_Request("nowhere"),
        dataset_id=node_id and node_id,
        node_id=node_id,
        field_name="associated_publications",
        idx=7,
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400
    assert _publications(state, node_id) == ["doi:first", "doi:second"]


@pytest.mark.asyncio
async def test_a_field_the_entity_does_not_have_is_refused(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.update_primitive_list_item(
        request=_Request("x"),
        dataset_id="d-1",
        node_id=node_id,
        field_name="no_such_field",
        idx=0,
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_single_entity_field_the_entity_does_not_have_is_refused(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """field_info raises KeyError for an unknown name; that must not be a 500."""
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.update_single_entity_field(
        request=_Request("x"),
        dataset_id="d-1",
        node_id=node_id,
        field_name="no_such_field",
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_deleting_a_single_entity_field_the_entity_does_not_have_is_refused(
    investigation_with_publications: tuple[AppState, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, node_id = investigation_with_publications
    monkeypatch.setattr(table_routes, "save_dataset_state", _never_saves)

    response = await table_routes.delete_single_entity_field(
        request=_Request(),
        dataset_id="d-1",
        node_id=node_id,
        field_name="no_such_field",
        dataset_state=(object(), state),
        user=None,
        session=None,
    )

    assert response.status_code == 400
