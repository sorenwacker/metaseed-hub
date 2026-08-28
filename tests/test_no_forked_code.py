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


# Templates that draw a *specification's* structure -- its entity definitions
# and the containment fields between them -- rather than a dataset's entities.
# Different data and a different picture: metaseed's graph.js renders the
# `{nodes, edges, entity_types}` a dataset facade produces, and has nothing to
# say about a spec that has no instances yet. They are not copies of it.
DRAW_A_SPECIFICATION_NOT_A_DATASET = {
    "spec_builder/view.html",
}


def test_no_template_draws_the_dataset_graph_itself() -> None:
    """The entity graph is metaseed's; a template redrawing it is a fork.

    `graph.html` carried ~280 lines of inlined vis.js drawing -- a lesser copy of
    `metaseed/ui/static/js/graph.js`, which has the legend with per-entity-type
    counts, click-a-type-to-hide, and the layout controls. The copy existed
    because the library's drawing could not be reused without its transport;
    that is no longer true, so a reappearance is drift rather than necessity.
    """
    from pathlib import Path

    templates = Path("src/metaseed_hub/ui/templates")
    offenders: list[str] = []
    for path in sorted(templates.rglob("*.html")):
        name = str(path.relative_to(templates))
        if name in DRAW_A_SPECIFICATION_NOT_A_DATASET:
            continue
        if "new vis.Network" in path.read_text():
            offenders.append(name)

    assert not offenders, (
        "these draw the dataset graph themselves rather than loading metaseed's "
        "graph.js:\n  " + "\n  ".join(offenders)
    )


def test_the_graph_page_loads_the_library_drawing() -> None:
    """Deleting the fork is only half of it; the page must use the real one."""
    from pathlib import Path

    page = Path("src/metaseed_hub/ui/templates/graph.html").read_text()

    assert "/hub/static/js/graph.js" in page, "the graph page must load metaseed's graph.js"
    assert "METASEED_GRAPH_URL" in page, (
        "the host supplies the data URL; that is the whole of its side of the contract"
    )


def test_the_explorer_panel_is_the_librarys() -> None:
    """The hub's explorer used to carry its own selectEntity -- a lesser copy of
    metaseed's panel that showed fields as name and type while metaseed's showed
    every attribute, the rules and the profile. The panel is one script the
    library serves; the hub loads it and defines none of it."""
    template = (
        Path(__file__).resolve().parent.parent / "src/metaseed_hub/ui/templates/explore/index.html"
    ).read_text()
    assert "/static/js/explore-panel.js" in template
    for own in ("function selectEntity", "function renderRule", "function renderFieldDetails"):
        assert own not in template, f"the hub defines {own} itself"
    assert 'id="rules-section"' in template and 'id="profile-section"' in template


def test_every_template_is_rendered_or_included_somewhere() -> None:
    """A template nothing renders is dead code that still gets edited.

    Seven were found at once: two copies of an explorer view that had drifted
    apart (one had the validation-rules list, the other did not), a compare
    page, three partials and a spec-builder start page -- none reachable from
    any route. Vulture cannot see templates, so this is their vulture.
    """
    from pathlib import Path

    root = Path("src/metaseed_hub")
    templates = root / "ui/templates"
    sources = "\n".join(
        path.read_text() for path in [*root.rglob("*.py"), *templates.rglob("*.html")]
    )
    dead = [
        str(path.relative_to(templates))
        for path in sorted(templates.rglob("*.html"))
        if str(path.relative_to(templates)) not in sources
    ]
    assert not dead, f"templates nothing renders, includes or extends: {dead}"
