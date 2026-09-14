"""A reader whose standard is not listed must be told where to ask.

The Standards tab on /hub/datasets/new lists what metaseed ships and said
nothing about the rest, so someone whose standard is absent had nothing to act
on. The profiles come from metaseed, so the request belongs in metaseed's issue
tracker rather than the hub's.

Matches the note metaseed's own picker carries (sorenwacker/metaseed#291).
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("src/metaseed_hub/ui/templates")
ISSUES = "https://github.com/sorenwacker/metaseed/issues"


def _read(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text()


def test_the_standards_picker_says_where_to_ask_for_a_missing_one() -> None:
    page = _read("dataset_new.html")

    assert 'data-testid="missing-profile-note"' in page, (
        "the standards list must say what to do when a standard is not there"
    )
    assert ISSUES in page, "the note must link the tracker where profiles are asked for"


def test_the_note_comes_before_the_standards_it_qualifies() -> None:
    """Below the grid it was 1500px down: the reader who needs it is the one
    who stopped scrolling."""
    page = _read("dataset_new.html")

    assert page.index('data-testid="missing-profile-note"') < page.index(
        'class="standards-grid"'
    ), "the note must be readable without scrolling every standard"


def test_the_note_sits_with_the_standards_not_the_other_sources() -> None:
    """Importing a file and published specs are separate answers; the note is
    about the shipped standards, so it belongs in that panel."""
    page = _read("dataset_new.html")
    standards_panel = page[page.index('id="panel-standards"') :]
    standards_panel = standards_panel[: standards_panel.index('id="panel-import"')]

    assert 'data-testid="missing-profile-note"' in standards_panel
