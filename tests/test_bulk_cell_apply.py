"""One value, a block of cells, one save.

A value that repeats down a column was previously entered cell by cell, each
edit its own request and its own dataset version. Applying a block writes
every target from a single request, so the change is one version and either
lands whole or not at all: a half-applied block is worse than a refused one,
because nothing on the page says which half went in.

Two cells are never written by a block apply, and the refusals are the point
of most of this file:

- the column naming the row's parent, because moving a row to another parent
  is a link change and `metaseed.facade.linking` owns those (ADR 005), not a
  cell fill;
- any target whose row disappeared meanwhile — the whole batch is refused
  rather than partly applied.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from metaseed import MetaseedClient

from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.routes import table as table_routes


class _Request:
    """A request whose body is the given JSON payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


def _dataset_with_two_studies() -> tuple[AppState, list[str]]:
    """An Investigation with two Studies, as an inline table would show them."""
    client = MetaseedClient("miappe", "1.1")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})

    state = AppState()
    state.profile = "miappe"
    state.version = "1.1"
    state.facade = client.facade
    state.invalidate_cache()

    study_model = state.facade.Study._model
    ids = []
    for n in (1, 2):
        draft = study_model.model_construct(
            unique_id=f"STU-{n}", title=f"S{n}", investigation_id="INV-1"
        )
        node = state.add_node("Study", draft, parent_id=inv.id, skip_validation=True)
        ids.append(node.id if hasattr(node, "id") else node)
    return state, ids


def _values(state: AppState, node_id: str) -> dict[str, Any]:
    return state.nodes_by_id[node_id].instance.model_dump(exclude_none=True)


async def _apply(
    state: AppState,
    targets: list[dict[str, str]],
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, AsyncMock]:
    saved = AsyncMock()
    monkeypatch.setattr(table_routes, "save_dataset_state", saved)
    response = await table_routes.apply_value_to_cells(
        request=_Request({"value": value, "targets": targets}),
        dataset_id="ds-1",
        dataset_state=(object(), state),
        user=None,
        session=object(),
    )
    return response, saved


@pytest.mark.asyncio
async def test_the_value_lands_in_every_selected_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ids = _dataset_with_two_studies()

    response, _ = await _apply(
        state,
        [{"node_id": node_id, "field": "title"} for node_id in ids],
        "drought trial",
        monkeypatch,
    )

    assert response.status_code == 200
    assert [_values(state, node_id)["title"] for node_id in ids] == [
        "drought trial",
        "drought trial",
    ]


@pytest.mark.asyncio
async def test_a_block_apply_is_one_save(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two cells, one version — not one version per cell."""
    state, ids = _dataset_with_two_studies()

    _, saved = await _apply(
        state,
        [{"node_id": node_id, "field": "title"} for node_id in ids],
        "one write",
        monkeypatch,
    )

    assert saved.await_count == 1


@pytest.mark.asyncio
async def test_the_parent_reference_column_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filling `investigation_id` would re-parent rows behind linking's back."""
    state, ids = _dataset_with_two_studies()

    response, saved = await _apply(
        state,
        [{"node_id": ids[0], "field": "investigation_id"}],
        "INV-ELSEWHERE",
        monkeypatch,
    )

    assert response.status_code == 400
    assert _values(state, ids[0])["investigation_id"] == "INV-1"
    assert saved.await_count == 0


@pytest.mark.asyncio
async def test_a_vanished_row_refuses_the_whole_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partly applied is the failure mode worth refusing: nothing is written."""
    state, ids = _dataset_with_two_studies()

    response, saved = await _apply(
        state,
        [
            {"node_id": ids[0], "field": "title"},
            {"node_id": "gone-while-you-selected", "field": "title"},
        ],
        "half a block",
        monkeypatch,
    )

    assert response.status_code == 400
    assert _values(state, ids[0])["title"] == "S1", "the surviving row was written"
    assert saved.await_count == 0


@pytest.mark.asyncio
async def test_an_unknown_field_refuses_the_whole_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ids = _dataset_with_two_studies()

    response, saved = await _apply(
        state,
        [{"node_id": ids[0], "field": "not_a_field"}],
        "x",
        monkeypatch,
    )

    assert response.status_code == 400
    assert saved.await_count == 0


@pytest.mark.asyncio
async def test_the_value_is_converted_per_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A number column stores a number, exactly as a single cell edit does."""
    state, ids = _dataset_with_two_studies()
    numeric = [
        name
        for name in state.facade.Study.all_fields
        if state.facade.Study.field_info(name).get("type") in ("integer", "float")
    ]
    if not numeric:
        pytest.skip("Study has no numeric column in this profile")

    await _apply(state, [{"node_id": ids[0], "field": numeric[0]}], "42", monkeypatch)

    assert _values(state, ids[0])[numeric[0]] == 42


@pytest.mark.asyncio
async def test_an_empty_value_clears_every_selected_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blanking a block is a legitimate edit, not a no-op."""
    state, ids = _dataset_with_two_studies()

    await _apply(
        state,
        [{"node_id": node_id, "field": "title"} for node_id in ids],
        "",
        monkeypatch,
    )

    assert all("title" not in _values(state, node_id) for node_id in ids)


def test_the_apply_control_is_reachable_from_the_table() -> None:
    """A capability with no control on the page is not a shipped feature."""
    from pathlib import Path

    template = Path("src/metaseed_hub/ui/templates/partials/inline_table.html").read_text()

    assert "Apply to selection" in template
    assert "bulk-apply" in template
    assert "/table/cells" in template


def test_the_browser_can_select_a_block() -> None:
    """Shift-click range selection and Escape live in the shipped script."""
    from pathlib import Path

    script = Path("src/metaseed_hub/ui/static/js/hub.js").read_text()

    assert "shiftKey" in script
    assert "cell-selected" in script
    # The endpoint is carried by the template's data-apply-url, not built in
    # JS, so the route and the page that calls it cannot drift apart silently.
    assert "applyUrl" in script


def test_the_parent_reference_rule_has_one_home() -> None:
    """The table renderer and the apply route must agree on what is read-only."""
    from pathlib import Path

    helpers = Path("src/metaseed_hub/ui/helpers/tables.py").read_text()
    routes = Path("src/metaseed_hub/ui/routes/table.py").read_text()

    assert "def parent_reference_field" in helpers
    assert "parent_reference_field" in routes
    assert routes.count('_id"') == 0 or "parent_reference_field" in routes
