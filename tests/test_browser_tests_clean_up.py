"""A browser test deletes the records it creates.

The selenium tests run against the developer's own stack, signed in as the demo
account, and none of them deleted anything: 77 datasets and 9 specification
drafts named `selenium-...` had piled up in one development database, burying
the developer's own records in the dataset list.

Cleanup hangs on two things a test author can get wrong without any test
failing, so both are gated here, in the default suite:

- a record named by hand is invisible to the cleanup, which finds records by
  the run's prefix, so names come from `record_name()` only;
- a `driver` fixture that does not call `delete_records()` leaves everything
  its test made.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


def _selenium_modules() -> list[Path]:
    return sorted(TESTS.glob("test_selenium*.py"))


def _hand_written_names(tree: ast.Module) -> list[int]:
    """Lines holding a string that starts a `selenium-` record name."""
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("selenium-")
        }
    )


def _driver_fixtures(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "driver"
        and any("fixture" in ast.unparse(decorator) for decorator in node.decorator_list)
    ]


def _calls_delete_records(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call) and ast.unparse(node.func).endswith("delete_records")
        for node in ast.walk(function)
    )


def test_there_are_selenium_modules_to_check() -> None:
    """A gate over an empty set passes for the wrong reason."""
    assert _selenium_modules()
    assert any(_driver_fixtures(ast.parse(path.read_text())) for path in _selenium_modules())


@pytest.mark.parametrize("path", _selenium_modules(), ids=lambda p: p.name)
def test_a_browser_test_names_its_records_through_record_name(path: Path) -> None:
    lines = _hand_written_names(ast.parse(path.read_text()))

    assert not lines, (
        f"{path.name} writes a 'selenium-' name by hand on line(s) {lines}. Use "
        "record_name() from tests/selenium_records.py: the cleanup finds records by "
        "the run's prefix and cannot see a name that lacks it."
    )


@pytest.mark.parametrize("path", _selenium_modules(), ids=lambda p: p.name)
def test_a_driver_fixture_deletes_the_records_of_its_test(path: Path) -> None:
    for fixture in _driver_fixtures(ast.parse(path.read_text())):
        assert _calls_delete_records(fixture), (
            f"the driver fixture in {path.name} does not call delete_records(), so "
            "whatever its tests create stays in the database they ran against."
        )
