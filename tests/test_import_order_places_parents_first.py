"""A child's type must be imported after the type that contains it.

An imported row finds its parent among the nodes created earlier in the pass, so
the order the entity types are walked in decides whether the tree survives.

A breadth-first walk from the root gets that right only while each type has one
containment path. ENA's ``File`` has two — ``Run`` contains it and so does
``Analysis`` — and breadth-first reaches it at depth 2 through ``Analysis``,
which sorts before ``Experiment`` and therefore before ``Run`` at the same
depth. Every File belonging to a Run was then created before any Run existed,
reported as ``_parent ... matches no imported entity``, and re-rooted.

Round-tripping the shipped ENA example through Excel lost all 12 File-under-Run
links that way. It did not show as unconnected nodes, because ``File.run_ref``
still draws a reference edge, so the picture looked almost right while the tree
was wrong.

Ordering by a topological sort of the containment graph — every type after
every type that contains it — is the property that actually matters.
"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path
from typing import Any

import metaseed
import yaml
from metaseed.profiles import ProfileFactory
from metaseed.ui.services.export import build_workbook_from_facade

from metaseed_hub.ui.helpers import add_entities_in_order, parse_workbook_sheets
from metaseed_hub.ui.helpers.entity_import import _child_types, _containment_order
from metaseed_hub.ui.metaseed_ui import AppState

#: The ENA example metaseed ships, read from the installed package: it is the
#: dataset a user actually round-trips, and it exercises every entity type.
EXAMPLE = Path(metaseed.__file__).parent / "examples/ena/1.0/arabidopsis-drought-rnaseq.yaml"


def _ena_facade() -> Any:
    facade = ProfileFactory().create("ena", "1.0")
    facade.load_nested(copy.deepcopy(yaml.safe_load(EXAMPLE.read_text())), "Study")
    return facade


def test_every_type_is_ordered_after_the_types_that_contain_it() -> None:
    facade = _ena_facade()
    order = _containment_order(facade, "Study")
    position = {entity_type: index for index, entity_type in enumerate(order)}

    violations = [
        f"{child} ({position[child]}) before its parent {parent} ({position[parent]})"
        for parent in order
        for child in sorted(_child_types(facade, parent))
        if child in position and position[child] < position[parent]
    ]

    assert violations == [], violations


def test_a_type_reachable_through_two_parents_follows_the_later_one() -> None:
    """ENA's File is contained by Analysis and by Run; it must follow both."""
    order = _containment_order(_ena_facade(), "Study")

    assert order.index("File") > order.index("Run")
    assert order.index("File") > order.index("Analysis")


def test_every_declared_type_still_appears_exactly_once() -> None:
    """Reordering must not drop a type, or its rows import as unknown."""
    facade = _ena_facade()
    order = _containment_order(facade, "Study")

    assert sorted(order) == sorted(facade.entities)
    assert len(order) == len(set(order))


def test_an_excel_round_trip_keeps_every_file_under_its_run() -> None:
    """The symptom: 12 File rows re-rooted, reported and then ignored."""
    facade = _ena_facade()
    buffer = BytesIO()
    build_workbook_from_facade(facade).save(buffer)

    state = AppState(profile="ena", version="1.0")
    reloaded = state.get_or_create_facade()
    _imported, errors = add_entities_in_order(
        state,
        reloaded,
        parse_workbook_sheets(buffer.getvalue(), profile="ena", version="1.0", facade=reloaded),
        "Study",
    )

    assert errors == [], errors[:5]


def test_the_round_trip_keeps_the_containment_edges() -> None:
    """A re-rooted File still drew a reference edge, so the loss was invisible
    in the picture. Count the containment edges, which is what actually broke."""
    from metaseed_hub.ui.services.graph import build_graph

    facade = _ena_facade()
    before = build_graph(facade)
    buffer = BytesIO()
    build_workbook_from_facade(facade).save(buffer)

    state = AppState(profile="ena", version="1.0")
    reloaded = state.get_or_create_facade()
    add_entities_in_order(
        state,
        reloaded,
        parse_workbook_sheets(buffer.getvalue(), profile="ena", version="1.0", facade=reloaded),
        "Study",
    )
    after = build_graph(reloaded)

    def containment(graph: dict[str, Any]) -> int:
        return len([e for e in graph["edges"] if not e.get("dashes")])

    assert containment(after) == containment(before)
