"""Every write stores what the user entered; validation reports it later.

The hub's write paths are permissive by design (docs/developer/architecture.md,
"Mutate"): the entity form, adding a row, bulk edits and every MCP tool pass
``skip_validation=True``, so an incomplete or wrong value is kept and shown by
validation instead of being thrown away. The six inline-table routes built
their instance with ``model_construct`` and then called ``state.update_node``
without it, which revalidates the whole entity through the facade. Production
logged the result: an ORCID entered as ``https://orcid.org/...`` where the
profile expects the bare identifier ended the request with a 500, and nothing
was saved. The same happened to any later table edit on an entity that already
held such a value -- stored earlier through the form or a paste -- so one bad
value made the entity's tables uneditable.

The behavioural tests call each route on such an entity. The gate keeps the
next route from reaching past the helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import HTMLResponse
from metaseed import MetaseedClient

from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.routes import table as table_routes

ORCID_URL = "https://orcid.org/0009-0001-6343-6990"
SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"


class _Request:
    """A form-posting request."""

    def __init__(self, form: dict[str, str] | None = None) -> None:
        self._form = form or {}

    async def form(self) -> dict[str, str]:
        return self._form


async def _saves(*args: Any, **kwargs: Any) -> None:
    """Stand-in for the database save; the tests read the in-memory state."""


def _state_for(client: MetaseedClient, profile: str, version: str) -> AppState:
    state = AppState()
    state.profile = profile
    state.version = version
    state.facade = client.facade
    state.invalidate_cache()
    return state


def _values(state: AppState, node_id: str) -> dict[str, Any]:
    return state.nodes_by_id[node_id].instance.model_dump(exclude_none=True)


@pytest.fixture
def person() -> tuple[AppState, str]:
    """A seek 1.0 Person with a valid ORCID."""
    client = MetaseedClient("seek", "1.0")
    client.create_entity("Person", {"first_name": "Alex", "last_name": "Example"})
    state = _state_for(client, "seek", "1.0")
    return state, next(iter(state.nodes_by_id))


@pytest.fixture
def investigation_holding_a_bad_date() -> tuple[AppState, str]:
    """A MIAPPE 1.1 Investigation that already stores a date the profile rejects.

    It got there the way production data does: through a permissive write.
    """
    client = MetaseedClient("miappe", "1.1")
    client.create_entity(
        "Investigation",
        {
            "unique_id": "INV-1",
            "title": "T",
            "submission_date": "15.03.2024",
            "associated_publications": ["doi:first", "doi:second"],
        },
        skip_validation=True,
    )
    state = _state_for(client, "miappe", "1.1")
    return state, next(iter(state.nodes_by_id))


@pytest.fixture
def study_with_a_responsible_person() -> tuple[AppState, str]:
    """A seek 1.0 Study; ``person_responsible`` is a single embedded Person."""
    client = MetaseedClient("seek", "1.0")
    client.create_entity("Study", {"title": "S"}, skip_validation=True)
    state = _state_for(client, "seek", "1.0")
    return state, next(iter(state.nodes_by_id))


def _route_kwargs(state: AppState, **kwargs: Any) -> dict[str, Any]:
    return {
        "dataset_id": "d-1",
        "dataset_state": (object(), state),
        "user": None,
        "session": None,
        **kwargs,
    }


@pytest.mark.asyncio
async def test_an_orcid_entered_as_a_url_is_stored_as_typed(
    person: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production case: a 500, and the value was lost."""
    state, node_id = person
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    response = await table_routes.update_table_cell(
        request=_Request({"orcid": ORCID_URL}), **_route_kwargs(state, node_id=node_id)
    )

    assert response.status_code == 200
    assert _values(state, node_id)["orcid"] == ORCID_URL


@pytest.mark.asyncio
async def test_a_cell_edit_on_an_entity_holding_a_bad_value_is_saved(
    investigation_holding_a_bad_date: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, node_id = investigation_holding_a_bad_date
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    response = await table_routes.update_table_cell(
        request=_Request({"title": "Renamed"}), **_route_kwargs(state, node_id=node_id)
    )

    assert response.status_code == 200
    assert _values(state, node_id)["title"] == "Renamed"
    assert _values(state, node_id)["submission_date"] == "15.03.2024", "the stored value is kept"


@pytest.mark.asyncio
async def test_adding_a_list_row_on_an_entity_holding_a_bad_value_is_saved(
    investigation_holding_a_bad_date: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, node_id = investigation_holding_a_bad_date
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    await table_routes.add_table_row(
        request=_Request(),
        **_route_kwargs(state, parent_node_id=node_id, field_name="associated_publications"),
    )

    assert len(_values(state, node_id)["associated_publications"]) == 3


@pytest.mark.asyncio
async def test_adding_an_entity_row_stores_the_child_and_returns_its_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entity-list branch of add_table_row, driven through the route."""
    client = MetaseedClient("seek", "1.0")
    client.create_entity("Project", {"title": "P"}, skip_validation=True)
    state = _state_for(client, "seek", "1.0")
    project_id = next(iter(state.nodes_by_id))
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    response = await table_routes.add_table_row(
        request=_Request(), **_route_kwargs(state, parent_node_id=project_id, field_name="members")
    )

    assert response.status_code == 200
    assert "<tr" in response.body.decode()
    assert [c.entity_type for c in state.nodes_by_id[project_id].children] == ["Person"]


@pytest.mark.asyncio
async def test_editing_a_list_item_on_an_entity_holding_a_bad_value_is_saved(
    investigation_holding_a_bad_date: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, node_id = investigation_holding_a_bad_date
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    response = await table_routes.update_primitive_list_item(
        request=_Request({"value": "doi:changed"}),
        **_route_kwargs(state, node_id=node_id, field_name="associated_publications", idx=0),
    )

    assert response.status_code == 200
    assert _values(state, node_id)["associated_publications"][0] == "doi:changed"


@pytest.mark.asyncio
async def test_deleting_a_list_item_on_an_entity_holding_a_bad_value_is_saved(
    investigation_holding_a_bad_date: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, node_id = investigation_holding_a_bad_date
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)
    # The route answers with the re-rendered table; what is stored is under test here.
    monkeypatch.setattr(table_routes, "_inline_table_fragment", lambda *a, **k: HTMLResponse("ok"))

    response = await table_routes.delete_primitive_list_item(
        request=_Request(),
        **_route_kwargs(state, node_id=node_id, field_name="associated_publications", idx=0),
    )

    assert response.status_code == 200
    assert _values(state, node_id)["associated_publications"] == ["doi:second"]


@pytest.mark.asyncio
async def test_a_single_entity_field_given_a_bad_value_is_stored_as_typed(
    study_with_a_responsible_person: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production logged the same 500 here, for a value outside an enumeration."""
    state, node_id = study_with_a_responsible_person
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)

    response = await table_routes.update_single_entity_field(
        request=_Request({"first_name": "Alex", "orcid": ORCID_URL}),
        **_route_kwargs(state, node_id=node_id, field_name="person_responsible"),
    )

    assert response.status_code == 200
    assert _values(state, node_id)["person_responsible"]["orcid"] == ORCID_URL


@pytest.mark.asyncio
async def test_clearing_a_single_entity_field_holding_a_bad_value_is_saved(
    study_with_a_responsible_person: tuple[AppState, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, node_id = study_with_a_responsible_person
    monkeypatch.setattr(table_routes, "save_dataset_state", _saves)
    await table_routes.update_single_entity_field(
        request=_Request({"first_name": "Alex", "orcid": ORCID_URL}),
        **_route_kwargs(state, node_id=node_id, field_name="person_responsible"),
    )

    response = await table_routes.delete_single_entity_field(
        request=_Request(), **_route_kwargs(state, node_id=node_id, field_name="person_responsible")
    )

    assert response.status_code == 200
    assert "person_responsible" not in _values(state, node_id)


# --- gate -------------------------------------------------------------------

TREE_WRITES = {"add_node", "update_node"}
CLIENT_WRITES = {"create_entity", "update_entity"}
TREE_HELPERS = SRC / "ui" / "helpers" / "tree.py"
# These author profile specifications, not dataset entities: SpecBuilder's
# add_entity/update_entity define an entity type and share only the name.
SPEC_AUTHORING = (SRC / "mcp" / "_spec_tools.py", SRC / "ui" / "spec_builder")


def _write_calls() -> list[tuple[Path, int, str, bool]]:
    """Every call to a tree or client write: (file, line, method, passes skip_validation=True)."""
    calls = []
    for path in SRC.rglob("*.py"):
        if any(path == excluded or excluded in path.parents for excluded in SPEC_AUTHORING):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in TREE_WRITES | CLIENT_WRITES:
                continue
            permissive = any(
                kw.arg == "skip_validation"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            calls.append((path, node.lineno, node.func.attr, permissive))
    return calls


def test_the_gate_sees_the_write_calls() -> None:
    """A scan that finds nothing passes vacuously; the helpers' own calls must be seen."""
    assert any(path == TREE_HELPERS for path, *_ in _write_calls())


def test_tree_writes_go_through_the_helpers() -> None:
    """``add_node``/``update_node`` belong to ``ui/helpers/tree.py``.

    The helpers there pass ``skip_validation``.
    """
    offenders = [
        f"{path.relative_to(SRC)}:{line} calls {method}"
        for path, line, method, _ in _write_calls()
        if method in TREE_WRITES and path != TREE_HELPERS
    ]
    assert not offenders, "use add_entity_node/update_entity_node instead:\n" + "\n".join(offenders)


def test_every_entity_write_is_permissive() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{line} calls {method} without skip_validation=True"
        for path, line, method, permissive in _write_calls()
        if not permissive
    ]
    assert not offenders, "\n".join(offenders)
