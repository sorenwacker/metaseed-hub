"""A selenium module must not import selenium at module scope unguarded.

selenium lives in `[project.optional-dependencies]`, so it is absent from any
environment synced without the extra. Collection happens before marker
deselection, so `-m "not selenium"` cannot save the run: pytest imports the
module, the import fails, and the whole session aborts with zero tests run.

`make test` is exactly that command, so a developer whose environment lost the
extra gets no tests and an error naming selenium rather than the missing extra.
Two of the three selenium modules already call `pytest.importorskip`; the third
did not, which is what this gate is here to stop recurring.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


def _selenium_modules() -> list[Path]:
    return sorted(TESTS.glob("test_selenium*.py"))


def _guards_before_import(tree: ast.Module) -> bool:
    """Whether `pytest.importorskip("selenium")` precedes any selenium import."""
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "importorskip"
                and node.value.args
                and getattr(node.value.args[0], "value", None) == "selenium"
            ):
                return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("selenium"):
            return False
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("selenium") for alias in node.names
        ):
            return False
    return True


def test_there_are_selenium_modules_to_check() -> None:
    """A gate over an empty set passes for the wrong reason."""
    assert _selenium_modules()


@pytest.mark.parametrize("path", _selenium_modules(), ids=lambda p: p.name)
def test_a_selenium_module_skips_rather_than_aborting_collection(path: Path) -> None:
    tree = ast.parse(path.read_text())

    assert _guards_before_import(tree), (
        f"{path.name} imports selenium at module scope without a preceding "
        'pytest.importorskip("selenium"). Without the extra installed, pytest '
        "aborts collection and the whole suite runs zero tests."
    )
