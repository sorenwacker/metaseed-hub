"""Delete stands alone at the foot of the dataset sidebar.

It sat in the block of routine actions, directly under Import and the export
buttons, the one destructive control among them.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path("src/metaseed_hub/ui/templates/dataset.html")


def test_delete_comes_after_the_entity_tree_outside_the_actions() -> None:
    html = TEMPLATE.read_text()
    sidebar = html[: html.index("</aside>")]
    actions = sidebar[sidebar.index("sidebar-actions") : sidebar.index("sidebar-section-grow")]

    assert "hx-delete" not in actions, "Delete is not one of the routine actions"
    foot = sidebar[sidebar.index("sidebar-section-grow") :]
    assert 'class="sidebar-section sidebar-danger"' in foot
    assert "hx-delete" in foot[foot.index("sidebar-danger") :]


def test_the_foot_is_pushed_to_the_bottom() -> None:
    css = Path("src/metaseed_hub/ui/static/css/hub.css").read_text()
    rule = css[css.index(".sidebar-danger {") :]
    assert "margin-top: auto" in rule[: rule.index("}")]
