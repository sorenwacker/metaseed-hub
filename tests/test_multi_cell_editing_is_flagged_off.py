"""Block selection is behind MULTI_CELL_EDITING, and it is off (260817).

The selection gesture does not work in the browser yet, so the control must not
appear: a button that does nothing is worse than an absent one, and the "+ Add
Row" button sat beside it in the header. The flag gates the button, the body
attribute that arms the script, and therefore the whole feature.
"""

from __future__ import annotations

from metaseed_hub.config import Settings


def test_the_flag_defaults_off() -> None:
    assert Settings().multi_cell_editing is False


def test_the_template_gates_the_control() -> None:
    from pathlib import Path

    template = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "metaseed_hub"
        / "ui"
        / "templates"
        / "partials"
        / "inline_table.html"
    ).read_text()

    assert "{% if multi_cell_editing %}" in template
    before = template.split("bulk-apply")[0]
    assert before.rstrip().endswith("{% if multi_cell_editing %}") or (
        "{% if multi_cell_editing %}" in before
    )


def test_the_script_checks_the_body_attribute() -> None:
    from pathlib import Path

    script = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "metaseed_hub"
        / "ui"
        / "static"
        / "js"
        / "hub.js"
    ).read_text()

    assert "multiCellEditingEnabled" in script
    assert "dataset.multiCellEditing" in script
