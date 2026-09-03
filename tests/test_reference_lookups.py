"""A reference field offers the rows it may name, in the application too.

The exported spreadsheet turns a reference column into a dropdown of the rows
that exist, because typing one by hand is the commonest way an import arrives
with something attached to nothing. The tables in the application had no such
help: the standalone gained a lookup, the hub's copy of the table template
predated it, and the hub had no endpoint to feed one anyway.
"""

from __future__ import annotations

from pathlib import Path

from metaseed_hub.ui.helpers.tables import _reference_fields

TABLE = Path("src/metaseed_hub/ui/templates/partials/inline_table.html")


class TestTheBuilderKnowsWhatNamesWhat:
    def test_a_reference_column_reports_its_target(self) -> None:
        from metaseed import ProfileFacade

        facade = ProfileFacade("ena", "1.0")
        refs = _reference_fields(facade.Sample)

        assert refs["study_ref"] == {
            "target_entity": "Study",
            "target_field": "alias",
        }

    def test_an_entity_naming_nothing_has_no_references(self) -> None:
        from metaseed import ProfileFacade

        facade = ProfileFacade("ena", "1.0")
        assert _reference_fields(facade.Study) == {}


class TestTheTableOffersTheLookup:
    def test_a_reference_cell_carries_the_lookup_attributes(self) -> None:
        markup = TABLE.read_text()

        assert "data-lookup=" in markup, "reference cells offer no lookup"
        assert "data-lookup-field=" in markup

    def test_the_lookup_asks_this_dataset(self) -> None:
        """Values from another dataset would be useless and a disclosure."""
        markup = TABLE.read_text()

        assert 'data-lookup-url="/hub/datasets/{{ dataset_id }}/lookup/' in markup

    def test_reference_and_ontology_cells_become_lookups(self) -> None:
        """A cell gets the lookup-input class, and so the autocomplete, when the
        column names another entity (a reference) or is an ontology term."""
        markup = TABLE.read_text()

        assert (
            "{% if reference_fields.get(col) or col in ontology_fields %} lookup-input{% endif %}"
            in markup
        )


class TestTheEndpointIsScoped:
    def test_the_route_is_under_a_dataset(self) -> None:
        from metaseed_hub.main import create_app

        app = create_app()
        paths = {
            route.path
            for mount in app.routes
            for route in getattr(getattr(mount, "app", None), "routes", [])
        }

        assert "/datasets/{dataset_id}/lookup/{entity_type}" in paths
