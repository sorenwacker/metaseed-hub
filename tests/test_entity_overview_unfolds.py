"""The entity overview opens folded and unfolds a level or a batch at a time.

It listed every entity of the dataset in one flat table, so a study with
several hundred runs was a page of runs with the rest of the dataset beneath
it. See docs/datasets/entities.md, *The entity overview*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from metaseed_hub.ui.routes.dataset.editor import OVERVIEW_BATCH, entity_overview

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui"
OVERVIEW = PACKAGE / "templates" / "partials" / "dataset_overview.html"


def _node(node_id: str, entity_type: str, children: list[dict[str, Any]] | None = None) -> dict:
    return {
        "id": node_id,
        "label": node_id,
        "entity_type": entity_type,
        "children": children or [],
    }


def _study_with_runs(runs: int) -> list[dict[str, Any]]:
    """Investigation > Study > Experiment, the Experiment holding ``runs`` Runs."""
    experiment = _node("exp", "Experiment", [_node(f"run-{n}", "Run") for n in range(runs)])
    return [_node("inv", "Investigation", [_node("study", "Study", [experiment])])]


def _entities(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in overview["rows"] if row["kind"] == "entity"}


def test_roots_and_their_direct_children_are_listed_on_arrival() -> None:
    rows = _entities(entity_overview(_study_with_runs(3)))

    assert [node for node, row in rows.items() if not row["hidden"]] == ["inv", "study"]
    assert rows["inv"]["expanded"] and not rows["study"]["expanded"]


def test_a_row_knows_its_parent_and_how_many_it_holds() -> None:
    rows = _entities(entity_overview(_study_with_runs(3)))

    assert rows["exp"]["parent"] == "study"
    assert rows["exp"]["child_count"] == 3
    assert rows["run-2"]["index"] == 2
    assert rows["inv"]["parent"] == ""


def test_the_counts_cover_the_whole_dataset_whatever_is_folded() -> None:
    overview = entity_overview(_study_with_runs(OVERVIEW_BATCH + 70))

    assert dict(overview["counts"])["Run"] == OVERVIEW_BATCH + 70


def test_a_long_list_of_children_gets_a_show_more_row_after_them() -> None:
    overview = entity_overview(_study_with_runs(OVERVIEW_BATCH + 70))
    more = [row for row in overview["rows"] if row["kind"] == "more"]

    assert [(row["parent"], row["remaining"]) for row in more] == [("exp", 70)]
    assert overview["rows"][-1] is more[0], "the row closes the list it extends"
    assert more[0]["hidden"], "its parent is folded on arrival"


def test_a_short_list_of_children_gets_no_show_more_row() -> None:
    overview = entity_overview(_study_with_runs(OVERVIEW_BATCH))

    assert all(row["kind"] == "entity" for row in overview["rows"])


def test_many_roots_are_batched_too() -> None:
    overview = entity_overview(
        [_node(f"inv-{n}", "Investigation") for n in range(OVERVIEW_BATCH + 5)]
    )
    rows = _entities(overview)

    assert sum(1 for row in rows.values() if not row["hidden"]) == OVERVIEW_BATCH
    more = overview["rows"][-1]
    assert (more["kind"], more["parent"], more["remaining"], more["hidden"]) == (
        "more",
        "",
        5,
        False,
    )


def test_the_page_offers_the_controls_the_documentation_names() -> None:
    page = OVERVIEW.read_text()

    assert 'data-testid="overview-expand-all"' in page
    assert 'data-testid="overview-collapse-all"' in page
    assert 'data-testid="overview-toggle"' in page
    assert 'data-testid="overview-more"' in page
