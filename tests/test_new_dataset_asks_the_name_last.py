"""New Dataset asks the name after the standard and version.

The name field sat above the choices and the grid of standards was greyed
until it was filled, while the local metaseed app, on the owner's request of
261009, asks the name once the standard and version are chosen. The hub now
asks in the same order, in a dialog, as its Builder's New page already does.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path("src/metaseed_hub/ui/templates/dataset_new.html")


def test_the_name_is_asked_in_a_dialog_after_the_choices() -> None:
    page = TEMPLATE.read_text()
    assert 'id="name-dialog"' in page
    assert page.index('id="dataset-name"') > page.index('id="name-dialog"'), (
        "the name field belongs to the dialog that follows the choice"
    )
    assert page.index('id="name-dialog"') > page.index('id="panel-standards"'), (
        "nothing asks for a name before the ways to start"
    )
    assert (
        "standard-card.disabled" not in Path("src/metaseed_hub/ui/static/css/hub.css").read_text()
    )


def test_every_way_to_start_asks_for_the_name() -> None:
    script = TEMPLATE.read_text().split("<script>")[-1]
    for way in (
        "createDataset(",
        "createDatasetWithExample(",
        "createDatasetFromSpec(",
        "handleImport(",
    ):
        body = script[script.index("function " + way) :]
        body = body[: body.index("\n}\n")]
        assert "askName(" in body, way


def test_a_repository_import_asks_no_name() -> None:
    page = TEMPLATE.read_text()
    assert "so no name is asked" in page
