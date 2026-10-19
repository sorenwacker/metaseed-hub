"""The hub's explorer sidebar shows each profile's version of a differing rule.

metaseed's ``renderRules`` lists the rules the compared profiles disagree on
only when the page hands it ``rule_diffs``; called without it, the sidebar
showed the base profile's rules alone, as before metaseed 0.64.
"""

from __future__ import annotations

from metaseed_hub.ui.app import TEMPLATES_DIR

EXPLORE_TEMPLATE = TEMPLATES_DIR / "explore" / "index.html"


def test_the_sidebar_is_given_the_rule_differences() -> None:
    template = EXPLORE_TEMPLATE.read_text()

    assert "renderRules(" in template
    assert "data.graph.rule_diffs" in template, "the sidebar lists only the base profile's rules"
