"""No hub module may re-implement what the library already does.

The export was a fork: 194 lines holding the hub's own ``build_workbook``,
``_escape_formula`` and ``_format_cell_value``, comments copied verbatim,
differing from metaseed's only in taking a facade where the library's took an
application state object. It stayed correct and stopped improving — dropdowns
for controlled vocabularies, cross-sheet reference pickers, tables, headings
carrying their descriptions all landed in the library and reached the standalone
application, while the hub kept exporting bare grids.

The library now builds from a facade, which both applications hold, and the copy
is gone. This gate is here so the next one is caught while it is one function
long, in the same spirit as the template gate next door.
"""

from __future__ import annotations

import ast
from pathlib import Path

HUB_SRC = Path("src/metaseed_hub")

#: Modules that may build a workbook themselves, with the reason. Empty: the
#: library builds workbooks, and the hub asks it to.
MAY_BUILD_WORKBOOKS: dict[str, str] = {}

#: Function names the library owns. A hub module defining one of these is
#: re-implementing it, whatever the body says.
LIBRARY_FUNCTIONS = frozenset(
    {
        "_escape_formula",
        "_format_cell_value",
        "build_workbook_from_facade",
        "collect_entities_by_type",
    }
)


def _python_files() -> list[Path]:
    return sorted(HUB_SRC.rglob("*.py"))


def _type_checking_lines(tree: ast.AST) -> set[int]:
    """Line numbers inside ``if TYPE_CHECKING:`` blocks.

    Imports there exist for annotations and are erased at runtime, so they
    cannot be building anything.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        named = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if named:
            for child in node.body:
                for inner in ast.walk(child):
                    if hasattr(inner, "lineno"):
                        lines.add(inner.lineno)
    return lines


def _defined_functions(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_no_module_reimplements_a_library_function() -> None:
    offenders: list[str] = []
    for path in _python_files():
        defined = _defined_functions(ast.parse(path.read_text()))
        for name in sorted(defined & LIBRARY_FUNCTIONS):
            offenders.append(f"{path}: {name}")

    assert not offenders, (
        "these re-implement functions the library owns; import them instead:\n  "
        + "\n  ".join(offenders)
    )


def test_only_the_library_builds_workbooks() -> None:
    """A hub module constructing an ``openpyxl`` workbook is writing an export.

    Reading one is fine — imports are checked, not every openpyxl use — because
    the import side genuinely differs between the two applications. An import
    under ``TYPE_CHECKING`` is fine too: naming the type a function returns is
    not building one.
    """
    offenders: list[str] = []
    for path in _python_files():
        module = str(path.relative_to(HUB_SRC))
        if module in MAY_BUILD_WORKBOOKS:
            continue
        tree = ast.parse(path.read_text())
        typing_only = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if node.lineno in typing_only if hasattr(node, "lineno") else False:
                continue
            imported_workbook = (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("openpyxl")
                and any(alias.name == "Workbook" for alias in node.names)
            )
            if imported_workbook:
                offenders.append(f"{module}: imports openpyxl.Workbook")

    assert not offenders, (
        "these build workbooks themselves rather than asking metaseed:\n  " + "\n  ".join(offenders)
    )


def test_the_export_delegates_rather_than_duplicating() -> None:
    """The hub's export module is a filename and a delegation, nothing more."""
    source = (HUB_SRC / "ui/services/export.py").read_text()

    assert "build_workbook_from_facade" in source, (
        "the hub no longer delegates the workbook to the library"
    )
    assert "number_format" not in source, (
        "cell-level formatting has crept back into the hub's export"
    )
