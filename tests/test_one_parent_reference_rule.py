"""The column a child row inherits from its parent is named in one place.

``parent_reference_field`` in ``ui/helpers/tables.py`` says its docstring is
where the rule lives once, so the table renderer and the row route mean the
same column. The renderer built the name by hand a few lines below it, and
the row route rebuilt it with an ``endswith("_id")`` check; nothing pinned the
three to one definition. Both now call the function.
"""

from __future__ import annotations

from pathlib import Path

from metaseed_hub.ui.helpers.tables import parent_reference_field

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
CALLERS = (SRC / "ui" / "helpers" / "tables.py", SRC / "ui" / "routes" / "table.py")


def test_the_rule_is_the_lowercased_parent_type() -> None:
    assert parent_reference_field("Study") == "study_id"


def test_both_callers_use_the_function() -> None:
    for path in CALLERS:
        source = path.read_text(encoding="utf-8")
        assert (
            source.count("parent_reference_field(") >= 2
            if path.name == "tables.py"
            else ("parent_reference_field(" in source)
        ), f"{path.name} does not call parent_reference_field"


def test_nobody_rebuilds_the_rule_by_hand() -> None:
    hand_written = ('.lower()}_id"', 'endswith("_id")')
    offenders = [
        f"{path.relative_to(SRC)}:{n}"
        for path in CALLERS
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(marker in line for marker in hand_written)
        and "def parent_reference_field" not in line
        and "return f" not in line
    ]
    assert not offenders, "call parent_reference_field:\n" + "\n".join(offenders)
