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


async def _write(
    state: AppState,
    cells: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, AsyncMock]:
    """Post a block of cells, each with its own value — the route's contract.

    Filling one value into a selection is this with the value repeated, which
    is what the page sends; pasting is this with the values differing. One
    server contract, so the two gestures cannot drift apart.
    """
    saved = AsyncMock()
    monkeypatch.setattr(table_routes, "save_dataset_state", saved)
    response = await table_routes.apply_value_to_cells(
        request=_Request({"cells": cells}),
        dataset_id="ds-1",
        dataset_state=(object(), state),
        user=None,
        session=object(),
    )
    return response, saved


async def _apply(
    state: AppState,
    targets: list[dict[str, str]],
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, AsyncMock]:
    """Fill one value into every target, as the Apply-to-selection control does."""
    return await _write(state, [{**t, "value": value} for t in targets], monkeypatch)


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


@pytest.mark.asyncio
async def test_a_paste_writes_a_different_value_per_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of paste: values that vary down the column."""
    state, ids = _dataset_with_two_studies()

    response, _ = await _write(
        state,
        [
            {"node_id": ids[0], "field": "title", "value": "first"},
            {"node_id": ids[1], "field": "title", "value": "second"},
        ],
        monkeypatch,
    )

    assert response.status_code == 200
    assert [_values(state, node_id)["title"] for node_id in ids] == ["first", "second"]


@pytest.mark.asyncio
async def test_a_paste_is_one_save(monkeypatch: pytest.MonkeyPatch) -> None:
    state, ids = _dataset_with_two_studies()

    _, saved = await _write(
        state,
        [
            {"node_id": ids[0], "field": "title", "value": "first"},
            {"node_id": ids[1], "field": "title", "value": "second"},
        ],
        monkeypatch,
    )

    assert saved.await_count == 1


@pytest.mark.asyncio
async def test_two_cells_of_one_row_are_written_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pasted row spans columns; rebuilding per cell would drop the earlier one."""
    state, ids = _dataset_with_two_studies()

    await _write(
        state,
        [
            {"node_id": ids[0], "field": "title", "value": "both"},
            {"node_id": ids[0], "field": "description", "value": "columns"},
        ],
        monkeypatch,
    )

    written = _values(state, ids[0])
    assert written["title"] == "both"
    assert written["description"] == "columns"


@pytest.mark.asyncio
async def test_a_pasted_value_the_column_cannot_hold_is_kept_as_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discarding it silently would lose data; validation reports it instead."""
    state, ids = _dataset_with_two_studies()
    numeric = [
        name
        for name in state.facade.Study.all_fields
        if state.facade.Study.field_info(name).get("type") in ("integer", "float")
    ]
    if not numeric:
        pytest.skip("Study has no numeric column in this profile")

    await _write(
        state,
        [{"node_id": ids[0], "field": numeric[0], "value": "not a number"}],
        monkeypatch,
    )

    assert _values(state, ids[0])[numeric[0]] == "not a number"


def test_the_browser_can_copy_and_paste_a_block() -> None:
    """Both clipboard gestures are wired to the same one-request write."""
    from pathlib import Path

    script = Path("src/metaseed_hub/ui/static/js/hub.js").read_text()

    assert "copy" in script and "paste" in script
    assert "clipboardData" in script
    assert "\\t" in script, "a copied block is tab separated, as spreadsheets read"


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


@pytest.mark.asyncio
async def test_an_oversized_block_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A selection is bounded by the visible table; an unbounded batch is not."""
    state, ids = _dataset_with_two_studies()

    response, saved = await _write(
        state,
        [
            {"node_id": ids[0], "field": "title", "value": "x"}
            for _ in range(table_routes.MAX_BLOCK_CELLS + 1)
        ],
        monkeypatch,
    )

    assert response.status_code == 400
    assert saved.await_count == 0
