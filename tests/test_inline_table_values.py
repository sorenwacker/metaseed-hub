"""What an editable cell holds is what gets saved, so it must be the real value.

`_build_entity_list_table` truncated long strings for display — `val[:47] +
"..."` — and the inline-table template rendered that same row value into an
editable `<input hx-trigger="change, blur">`. Focusing a long cell and tabbing
away persisted the truncated `…`-suffixed string over the stored one: reading
a dataset corrupted it.

Truncation is now the template's problem (CSS `text-overflow` on the cell),
never the value's: the row carries the full string.
"""

from __future__ import annotations

from typing import Any

from metaseed_hub.ui.helpers.tables import _build_entity_list_table


class _Node:
    def __init__(self, node_id: str, entity_type: str, instance: Any, children=()):
        self.id = node_id
        self.entity_type = entity_type
        self.instance = instance
        self.children = list(children)


class _Instance:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return dict(self._data)


class _Helper:
    def __init__(self, names: list[str]):
        self.all_fields = list(names)

    def field_info(self, name: str) -> dict[str, Any]:
        return {"type": "string", "required": False}


def test_a_long_value_reaches_the_row_uncut() -> None:
    long_value = "x" * 80
    child = _Node("n-1", "Comment", _Instance({"text": long_value}))
    parent = _Node("p-1", "Study", _Instance({}), children=[child])

    table = _build_entity_list_table(parent, "comments", "Comment", _Helper(["text"]))

    assert table["rows"][0]["text"] == long_value
    assert "..." not in table["rows"][0]["text"]


def test_the_template_truncates_visually_not_the_value() -> None:
    """The stylesheet owns the ellipsis; the input owns the truth."""
    from pathlib import Path

    template = Path("src/metaseed_hub/ui/templates/partials/inline_table.html").read_text()

    assert "cell-clip" in template or "text-overflow" in template


class TestPrimitiveDeleteRerenders:
    """Rows are positional: after a delete, every later row's `_idx` pointed at
    the wrong item, so the next edit overwrote a survivor and an edit past the
    new end was silently dropped. The route now returns the whole re-rendered
    table and the button swaps it in."""

    def test_the_route_returns_the_table_not_a_bare_200(self) -> None:
        import inspect

        from metaseed_hub.ui.routes import table as table_module

        source = inspect.getsource(table_module.delete_primitive_list_item)

        assert "_inline_table_fragment(" in source

    def test_the_button_swaps_the_whole_table(self) -> None:
        from pathlib import Path

        template = Path("src/metaseed_hub/ui/templates/partials/inline_table.html").read_text()
        marker = 'hx-delete="/hub/datasets/{{ dataset_id }}/table/{{ node_id }}/primitive/'
        primitive_delete = template.split(marker)[1]
        button = primitive_delete.split("</button>")[0]

        assert 'hx-target="#inline-table-' in button
        assert 'hx-swap="outerHTML"' in button
        assert 'hx-swap="delete"' not in button


class _OntologyHelper:
    """A helper whose one field is an ontology_term with declared ontologies."""

    all_fields = ["trait_term"]

    def field_info(self, name: str) -> dict[str, Any]:
        return {"type": "ontology_term", "ontologies": ["to", "co_321"], "required": False}


def test_an_ontology_column_carries_its_lookup_in_the_table() -> None:
    """An ontology field in a table cell must get the form's ontology lookup, not
    a plain text box; the table exposes the ontology columns so the template can
    wire lookup.js to them."""
    child = _Node("n-1", "ObservedVariable", _Instance({"trait_term": "TO:0001"}))
    parent = _Node("p-1", "Study", _Instance({}), children=[child])
    table = _build_entity_list_table(parent, "variables", "ObservedVariable", _OntologyHelper())
    assert table["ontology_fields"] == {"trait_term": "to,co_321"}


def test_a_new_row_ontology_cell_is_wired_for_lookup() -> None:
    """The "+ Add Row" builder produced plain inputs; an ontology cell in a new
    row now carries the ontology lookup attributes too."""
    from metaseed.ui.state import TreeNode

    from metaseed_hub.ui.routes.table_rows import _build_entity_row_html

    node = TreeNode(
        id="n-1", entity_type="ObservedVariable", instance=None, label="v", parent_id="p-1"
    )
    html = _build_entity_row_html(
        dataset_id="ds-1",
        field_name="variables",
        row_idx=0,
        child_node=node,
        nested_type="ObservedVariable",
        columns=["trait_term"],
        column_types={"trait_term": "ontology_term"},
        inherited_cols=set(),
        instance_data={},
        column_ontologies={"trait_term": "to,co_321"},
    )
    assert 'data-lookup-type="ontology"' in html
    assert "lookup-input" in html
    assert 'data-ontologies="to,co_321"' in html
