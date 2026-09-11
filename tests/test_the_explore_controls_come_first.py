"""The explorer's controls sit above everything that varies in length.

metaseed 0.51.0 moved the Explore button and the Show/Hide toggles above the
profile's description and its validation rules, because a paragraph and dozens
of rules pushed what a person came to press off the screen. The hub renders its
own copy of the page and the change never reached it. This gate holds the order
here; the library carries the same one.
"""

from __future__ import annotations

from metaseed_hub.ui.app import TEMPLATES_DIR


def test_the_explore_button_and_filters_precede_the_profile_and_its_rules() -> None:
    template = (TEMPLATES_DIR / "explore" / "index.html").read_text()
    controls = template.index('id="compare-btn"')
    filters = template.index('id="filter-section"')
    for section in ('id="profile-section"', 'id="rules-section"'):
        assert controls < template.index(section), section
        assert filters < template.index(section), section
