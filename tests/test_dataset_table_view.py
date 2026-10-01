"""The dataset list has a table view with search, filters and sorting.

Twenty cards in a grid are fine to look at and useless to search: the owner
looking for one dataset among many had to read every card. The table view
shows one row per dataset and narrows as you type; the filters live in the
page address, so a narrowed table can be bookmarked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.routing import Mount

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.ui.dataset_list import DatasetRow, ListFilters, apply_filters
from metaseed_hub.ui.dependencies import get_current_user_from_cookie

_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[])


def _row(
    name: str,
    profile: str = "ena",
    version: str = "1.0",
    *,
    entities: int = 0,
    access: str = "mine",
    days_ago: int = 0,
    description: str = "",
) -> DatasetRow:
    dataset = SimpleNamespace(
        id=name,
        name=name,
        description=description,
        profile=profile,
        version=version,
        updated_at=datetime(2026, 10, 1, tzinfo=UTC) - timedelta(days=days_ago),
    )
    return DatasetRow(dataset=dataset, entities=entities, access=access, collaboration=None)


def _names(**filters) -> list[str]:
    return [r.dataset.name for r in apply_filters(ROWS, ListFilters(**filters))]


ROWS = [
    _row("wheat-drought", "miappe", "1.2", entities=40, days_ago=3),
    _row(
        "tomato-lcms",
        "metabolights",
        "1.0",
        entities=12,
        access="shared",
        days_ago=1,
        description="leaf metabolome",
    ),
    _row("maize-imaging", "miappe-htp", "1.0", entities=0, access="collaboration", days_ago=2),
]


class TestFiltering:
    def test_the_default_order_is_newest_first(self) -> None:
        names = _names()
        assert names == ["tomato-lcms", "maize-imaging", "wheat-drought"]

    def test_search_matches_name_description_and_profile_whatever_the_case(self) -> None:
        assert _names(q="WHEAT") == ["wheat-drought"]
        assert _names(q="metabolome") == ["tomato-lcms"]
        assert set(_names(q="miappe")) == {
            "wheat-drought",
            "maize-imaging",
        }

    def test_profile_and_access_narrow_the_list(self) -> None:
        assert _names(profile="miappe") == ["wheat-drought"]
        assert _names(access="shared") == ["tomato-lcms"]
        assert [
            r.dataset.name for r in apply_filters(ROWS, ListFilters(access="collaboration"))
        ] == ["maize-imaging"]

    def test_every_column_sorts_both_ways(self) -> None:
        by_name = [
            r.dataset.name for r in apply_filters(ROWS, ListFilters(sort="name", direction="asc"))
        ]
        assert by_name == ["maize-imaging", "tomato-lcms", "wheat-drought"]
        by_entities = [
            r.dataset.name
            for r in apply_filters(ROWS, ListFilters(sort="entities", direction="desc"))
        ]
        assert by_entities == ["wheat-drought", "tomato-lcms", "maize-imaging"]
        by_profile = [
            r.dataset.name
            for r in apply_filters(ROWS, ListFilters(sort="profile", direction="asc"))
        ]
        assert by_profile == ["tomato-lcms", "wheat-drought", "maize-imaging"]

    def test_an_unknown_sort_or_view_falls_back_rather_than_failing(self) -> None:
        filters = ListFilters.from_query(
            {"sort": "colour", "dir": "sideways", "view": "cube"}, remembered="table"
        )
        assert filters.sort == "updated" and filters.direction == "desc" and filters.view == "table"
        assert ListFilters.from_query({}, remembered=None).view == "cards"


def _client() -> TestClient:
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: _TOKEN
    return TestClient(app)


async def test_the_table_view_lists_the_dataset_in_a_row(ena_dataset, app_db) -> None:
    html = _client().get("/hub/?view=table").text

    assert 'class="dataset-table"' in html
    assert f'href="/hub/datasets/{ena_dataset.id}"' in html
    for heading in ("Name", "Profile", "Version", "Entities", "Updated", "Access"):
        assert f">{heading}<" in html or f">{heading}</a>" in html


async def test_search_narrows_the_table_and_an_htmx_request_gets_only_the_list(
    ena_dataset, app_db
) -> None:
    client = _client()
    narrowed = client.get("/hub/?view=table&q=no-such-dataset", headers={"HX-Request": "true"})

    assert narrowed.status_code == 200
    assert "<html" not in narrowed.text
    assert f'href="/hub/datasets/{ena_dataset.id}"' not in narrowed.text
    assert "No datasets match" in narrowed.text


async def test_choosing_a_view_is_remembered_in_a_cookie(ena_dataset, app_db) -> None:
    first = _client().get("/hub/?view=table")

    assert first.cookies.get("dataset_view") == "table"


async def test_the_remembered_view_is_used_on_the_next_visit(ena_dataset, app_db) -> None:
    again = _client().get("/hub/", cookies={"dataset_view": "table"})

    assert 'class="dataset-table"' in again.text


async def test_the_cards_are_still_the_default(ena_dataset, app_db) -> None:
    html = _client().get("/hub/").text
    assert 'class="project-grid"' in html
    assert 'class="dataset-table"' not in html
