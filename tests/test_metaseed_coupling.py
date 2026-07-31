"""Gate test for the metaseed.ui import boundary (#53 step 4).

The hub consumes metaseed through its public API (``MetaseedClient``,
``ProfileFacade``). The internal UI layer ``metaseed.ui`` may be imported by
exactly one designated boundary module, ``metaseed_hub.ui.metaseed_ui``, which
re-exports the internals still in use. This test scans every module under
``src/metaseed_hub`` (including function-level imports) and fails on any other
import of ``metaseed.ui``.
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "metaseed_hub"
BOUNDARY_MODULE = SRC_ROOT / "ui" / "metaseed_ui.py"


def _metaseed_ui_imports(path: Path) -> list[str]:
    """Collect ``metaseed.ui`` imports anywhere in a module.

    Walks the full AST, so imports nested inside functions or
    ``TYPE_CHECKING`` blocks are found as well.

    Args:
        path: Python source file to scan.

    Returns:
        ``"<relative path>:<line>: <import statement>"`` entries for each hit.
    """
    hits: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    relative = path.relative_to(SRC_ROOT.parent.parent)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "metaseed.ui" or alias.name.startswith("metaseed.ui."):
                    hits.append(f"{relative}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "metaseed.ui" or module.startswith("metaseed.ui."):
                names = ", ".join(alias.name for alias in node.names)
                hits.append(f"{relative}:{node.lineno}: from {module} import {names}")
    return hits


def test_only_the_boundary_module_imports_metaseed_ui() -> None:
    """No module outside metaseed_hub.ui.metaseed_ui may import metaseed.ui."""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == BOUNDARY_MODULE:
            continue
        offenders.extend(_metaseed_ui_imports(path))
    assert not offenders, (
        "metaseed.ui may only be imported by metaseed_hub.ui.metaseed_ui; "
        "import from that module or use the public MetaseedClient/ProfileFacade API instead:\n"
        + "\n".join(offenders)
    )


def test_boundary_module_exports_the_remaining_internals() -> None:
    """The boundary module must exist and expose the names routes rely on."""
    from metaseed_hub.ui import metaseed_ui

    for name in ("AppState", "TreeNode", "METASEED_STATIC_DIR", "METASEED_TEMPLATES_DIR"):
        assert hasattr(metaseed_ui, name), f"metaseed_ui must export {name}"
    assert metaseed_ui.METASEED_STATIC_DIR.is_dir()
    assert metaseed_ui.METASEED_TEMPLATES_DIR.is_dir()


# Private attributes of metaseed objects the hub has reached for in the past.
# Each has a public equivalent; reaching past it couples the hub to metaseed's
# internals just as an internal import would, and static analysis cannot tell a
# metaseed object from a hub one, so the check is a named list rather than a
# blanket rule on leading underscores.
PRIVATE_METASEED_ATTRIBUTES = {"_model": "EntityHelper.model"}


def _private_attribute_uses(path: Path) -> list[str]:
    """Accesses of a known metaseed-private attribute in one module."""
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in PRIVATE_METASEED_ATTRIBUTES:
            public = PRIVATE_METASEED_ATTRIBUTES[node.attr]
            found.append(
                f"{path.relative_to(SRC_ROOT.parent.parent)}:{node.lineno} "
                f"uses .{node.attr}; use {public}"
            )
    return found


def test_no_module_reaches_past_a_public_attribute() -> None:
    """The hub uses metaseed's public surface, not the object's private one.

    ``EntityHelper._model`` was read in eight places while ``.model`` -- the
    same object -- is public. A private attribute can be renamed in a patch
    release without warning, so each use is a break waiting for an upgrade.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        offenders.extend(_private_attribute_uses(path))
    assert not offenders, "use metaseed's public attributes:\n" + "\n".join(offenders)
