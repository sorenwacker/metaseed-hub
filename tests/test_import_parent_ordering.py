"""Import order follows containment, not entity-declaration order.

``add_entities_in_order`` resolves a child's ``_parent`` against nodes created
earlier in the same pass, so a parent type must be processed before its
children. It used ``[root] + list(facade.entities)`` declaration order, which
guaranteed that only for the root: a grandchild whose parent type happened to be
declared later was re-rooted. ``_containment_order`` walks the containment graph
from the root instead.
"""

from __future__ import annotations

from metaseed_hub.ui.helpers.entity_import import _containment_order


class _FakeHelper:
    def __init__(self, children: dict[str, str]) -> None:
        # field name -> contained entity type
        self._children = children

    @property
    def all_fields(self) -> list[str]:
        return list(self._children)

    def field_info(self, name: str) -> dict[str, str]:
        return {"type": "list", "items": self._children[name]}


class _FakeFacade:
    """A three-level hierarchy Study -> Sample -> Assay, declared child-first so
    declaration order would place a child before its parent."""

    #: Deliberately not parent-first.
    entities = ["Assay", "Sample", "Study"]

    def __init__(self) -> None:
        self._helpers = {
            "Study": _FakeHelper({"samples": "Sample"}),
            "Sample": _FakeHelper({"assays": "Assay"}),
            "Assay": _FakeHelper({}),
        }

    def __getattr__(self, name: str) -> _FakeHelper:
        try:
            return self._helpers[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def test_a_parent_type_is_ordered_before_the_types_it_contains() -> None:
    order = _containment_order(_FakeFacade(), "Study")
    assert order.index("Study") < order.index("Sample") < order.index("Assay")


def test_every_type_is_present_even_when_unreachable_from_the_root() -> None:
    facade = _FakeFacade()
    facade._helpers["Orphan"] = _FakeHelper({})
    facade.entities = [*facade.entities, "Orphan"]
    order = _containment_order(facade, "Study")
    assert set(order) == {"Study", "Sample", "Assay", "Orphan"}
    assert order.index("Study") < order.index("Sample") < order.index("Assay")
