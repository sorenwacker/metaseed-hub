"""The hub looks terms up through the library's router, not through OLS.

The library made OLS one source among several — local vocabularies, a
consortium's own list, anything answering the ``TermSource`` questions. The hub
had its own copies of the same OLS-only calls, so a vocabulary configured on the
server would have been invisible in the hub's picker and to its MCP tools while
the standalone application offered it. This gate is the same one as metaseed's,
for the same rule, on this side of the boundary.

Two things are allowed to keep naming the OLS service: the catalogue listing
(which ontologies OLS hosts is a question about OLS) and the cache statistics
(the OLS adapter is the source that caches, because it is the one that goes over
the network).
"""

from __future__ import annotations

import ast
from pathlib import Path

HUB_SRC = Path("src/metaseed_hub")

#: Modules allowed to name the OLS service, with the reason.
MAY_USE_OLS_DIRECTLY: dict[str, str] = {
    "mcp/_ontology_tools.py": "lists the ontologies OLS hosts",
    "ui/routes/ontology_api.py": "reports the OLS adapter's cache statistics",
}

#: Lookup entry points that must reach whatever sources are configured.
MUST_ROUTE = {
    "ui/routes/ontology_api.py": [
        "search_ontology_terms",
        "suggest_ontology_terms",
        "get_ontology_term",
    ],
    "mcp/_ontology_tools.py": [
        "search_ontology",
        "get_ontology_term",
        "suggest_ontology_term",
    ],
}


def _function_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def test_no_module_reaches_for_the_ols_service() -> None:
    offenders: list[str] = []
    for path in sorted(HUB_SRC.rglob("*.py")):
        module = str(path.relative_to(HUB_SRC))
        if module in MAY_USE_OLS_DIRECTLY:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not (node.module or "").startswith("metaseed.services"):
                continue
            names = {alias.name for alias in node.names}
            if names & {"get_ontology_service", "OntologyService"}:
                offenders.append(f"{module}:{node.lineno}")

    assert not offenders, (
        "these depend on OLS being the term source; ask "
        "metaseed.services.terms.get_term_source() instead:\n  " + "\n  ".join(offenders)
    )


def test_the_hub_does_not_speak_ols_http_itself() -> None:
    """Except the catalogue call, which is a question about OLS."""
    offenders: list[str] = []
    for path in sorted(HUB_SRC.rglob("*.py")):
        module = str(path.relative_to(HUB_SRC))
        if module in MAY_USE_OLS_DIRECTLY:
            continue
        if "ebi.ac.uk/ols" in path.read_text():
            offenders.append(module)

    assert not offenders, (
        "these query OLS4 themselves rather than asking a term source:\n  " + "\n  ".join(offenders)
    )


def test_every_lookup_entry_point_asks_the_router() -> None:
    offenders: list[str] = []
    for module, names in MUST_ROUTE.items():
        path = HUB_SRC / module
        source = path.read_text()
        tree = ast.parse(source)
        for name in names:
            body = _function_source(tree, source, name)
            assert body, f"{module}: {name} no longer exists — update this gate"
            if "get_term_source" not in body:
                offenders.append(f"{module}: {name}")

    assert not offenders, (
        "these resolve terms without asking the configured sources:\n  " + "\n  ".join(offenders)
    )
