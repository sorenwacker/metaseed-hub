"""The hub's explorer page keeps no colour of its own.

The hub's copy of the explorer template carried the same table of colours per
diff state as the library's, and both painted nodes from it, so the canvas
stayed on the old scheme after the legend and the server changed. The server
sends complete styling and a state per edge, and metaseed's ``explore-graph.js``
draws it for every host. A per-state table here is the fork coming back.
"""

from __future__ import annotations

import re

from metaseed.specs.merge.models import DiffType

from metaseed_hub.ui.app import TEMPLATES_DIR

EXPLORE_TEMPLATE = TEMPLATES_DIR / "explore" / "index.html"


def test_the_template_has_no_colour_table_per_diff_state() -> None:
    template = EXPLORE_TEMPLATE.read_text()
    for state in [d.value for d in DiffType] + ["regular"]:
        assert not re.search(rf"\b{state}:\s*\{{", template), state
    assert "DIFF_COLORS" not in template
    assert "SPEC_BUILDER_COLORS" not in template


def test_the_template_draws_with_the_shared_graph_script() -> None:
    template = EXPLORE_TEMPLATE.read_text()
    assert "/hub/static/js/explore-graph.js" in template
    assert "edgeIsVisible(" in template
