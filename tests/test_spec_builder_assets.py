"""Spec-builder asset integrity and the metaseed reuse boundary.

The hub does not fork metaseed's spec-builder JavaScript. The graph engine
(``spec-builder-core.js``), the shared ERD module (``erd-common.js``), and the
ontology autocomplete widget (``ontology-autocomplete.js``) are loaded from the
mounted metaseed static directory; the hub's own ``spec-builder.js`` holds only
configuration and hub-specific glue. These tests pin that boundary:

- every ``<script src>`` in the spec-builder templates resolves to an existing
  file in one of the app's static mounts;
- the hub wiring script does not redefine functions the shared modules provide
  (the anti-fork gate);
- every function referenced by an inline handler in the spec-builder templates
  is defined in one of the scripts the page loads.
"""

import re
from pathlib import Path

from metaseed_hub.ui.app import STATIC_DIR, UI_DIR
from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR

TEMPLATES_DIR = UI_DIR / "templates"
SPEC_BUILDER_TEMPLATES_DIR = TEMPLATES_DIR / "spec_builder"
HUB_SPEC_BUILDER_JS = STATIC_DIR / "js" / "spec-builder.js"

# Browser URL prefix -> mounted directory. The app serves the hub mount at
# /hub-static ("hub-static") and metaseed's packaged static directory at
# /static ("metaseed-static"); behind the reverse proxy both are additionally
# reachable under the /hub root path, which the templates use.
MOUNT_PREFIXES = [
    ("/hub/hub-static/", STATIC_DIR),
    ("/hub-static/", STATIC_DIR),
    ("/hub/static/", METASEED_STATIC_DIR),
    ("/static/", METASEED_STATIC_DIR),
]

SCRIPT_SRC_RE = re.compile(r'<script[^>]*\bsrc="([^"]+)"')
INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL)
HANDLER_ATTR_RE = re.compile(r'\b(?:on[a-z]+|hx-on[:\w.-]*)="([^"]*)"')
CALLED_NAME_RE = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(")
DEFINED_NAME_RES = [
    re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*="),
]

# Names callable in inline handlers without a script defining them.
BUILTINS = {
    "alert",
    "confirm",
    "prompt",
    "parseInt",
    "parseFloat",
    "fetch",
    "encodeURIComponent",
    "decodeURIComponent",
    "setTimeout",
    "String",
    "Number",
    "Boolean",
    "Date",
    "Event",
}
JS_KEYWORDS = {
    "if",
    "for",
    "while",
    "return",
    "function",
    "switch",
    "catch",
    "typeof",
    "new",
    "in",
    "of",
    "do",
    "else",
    "try",
    "throw",
}

# Functions owned by metaseed's shared modules. The hub wiring script must not
# define any of them; the installed shared modules must define all of them.
SHARED_GRAPH_FUNCTIONS = [
    "buildGraphData",
    "buildNodeConfig",
    "buildNodeLabel",
    "buildEntityEdges",
    "storeOriginalColors",
    "createEdge",
    "attachNetworkEventHandlers",
    "selectEntity",
    "rebuildGraph",
    "refreshGraph",
    "deleteEntity",
    "updateEntity",
    "saveSpec",
    "submitAddEntityForm",
    "showContextMenu",
    "hideEntity",
    "showEntity",
    "showAllEntities",
    "updateHiddenCount",
    "switchSidebarTab",
    "showRuleModal",
    "hideRuleModal",
]
SHARED_AUTOCOMPLETE_FUNCTIONS = [
    "attachAutocomplete",
    "fetchSuggestions",
    "renderDropdown",
    "handleKeydown",
    "highlightOption",
    "selectOption",
]


def resolve_script_src(src: str) -> Path | None:
    """Map a template script URL to a file path under a static mount.

    Args:
        src: The src attribute value, possibly with a query string.

    Returns:
        The resolved path, or None when no mount prefix matches.
    """
    path = src.split("?")[0]
    for prefix, directory in MOUNT_PREFIXES:
        if path.startswith(prefix):
            return directory / path[len(prefix) :]
    return None


def spec_builder_templates() -> list[Path]:
    return sorted(SPEC_BUILDER_TEMPLATES_DIR.rglob("*.html"))


def defined_names(js_text: str) -> set[str]:
    names: set[str] = set()
    for pattern in DEFINED_NAME_RES:
        names.update(pattern.findall(js_text))
    return names


def handler_called_names(html_text: str) -> set[str]:
    names: set[str] = set()
    for handler in HANDLER_ATTR_RE.findall(html_text):
        names.update(CALLED_NAME_RE.findall(handler))
    return names - BUILTINS - JS_KEYWORDS


def loaded_script_paths(template: Path) -> list[Path]:
    """Script files a template loads, following its {% extends %} chain."""
    text = template.read_text()
    paths: list[Path] = []
    extends = re.search(r'{%\s*extends\s+"([^"]+)"\s*%}', text)
    if extends:
        paths.extend(loaded_script_paths(TEMPLATES_DIR / extends.group(1)))
    for src in SCRIPT_SRC_RE.findall(text):
        resolved = resolve_script_src(src)
        if resolved is not None:
            paths.append(resolved)
    return paths


def inline_script_text(template: Path) -> str:
    text = template.read_text()
    chunks = INLINE_SCRIPT_RE.findall(text)
    extends = re.search(r'{%\s*extends\s+"([^"]+)"\s*%}', text)
    if extends:
        chunks.append(inline_script_text(TEMPLATES_DIR / extends.group(1)))
    return "\n".join(chunks)


class TestScriptSrcsResolve:
    """Every <script src> in the spec-builder templates maps to a real file."""

    def test_all_script_srcs_exist(self) -> None:
        missing = []
        for template in spec_builder_templates():
            for src in SCRIPT_SRC_RE.findall(template.read_text()):
                resolved = resolve_script_src(src)
                if resolved is None:
                    missing.append(f"{template.name}: {src} matches no mount")
                elif not resolved.is_file():
                    missing.append(f"{template.name}: {src} -> {resolved} does not exist")
        assert not missing, "\n".join(missing)

    def test_base_template_loads_shared_modules_in_order(self) -> None:
        """base.html loads the shared modules before the hub wiring script."""
        text = (SPEC_BUILDER_TEMPLATES_DIR / "base.html").read_text()
        srcs = [s.split("?")[0] for s in SCRIPT_SRC_RE.findall(text)]
        expected_order = [
            "/hub/static/js/erd-common.js",
            "/hub/static/js/spec-builder-core.js",
            "/hub/static/js/ontology-autocomplete.js",
            "/hub/hub-static/js/spec-builder.js",
        ]
        positions = [srcs.index(s) for s in expected_order]
        assert positions == sorted(positions), f"wrong load order: {srcs}"


class TestNoFork:
    """The hub wiring script must not redefine shared module functions."""

    def test_hub_script_does_not_define_shared_functions(self) -> None:
        # Only function declarations count as forking; re-exports such as
        # ``window.hideEntity = graph.hideEntity`` are the intended wiring.
        hub_declared = set(DEFINED_NAME_RES[0].findall(HUB_SPEC_BUILDER_JS.read_text()))
        forked = sorted(set(SHARED_GRAPH_FUNCTIONS + SHARED_AUTOCOMPLETE_FUNCTIONS) & hub_declared)
        assert not forked, f"hub spec-builder.js redefines shared functions: {forked}"

    def test_installed_core_defines_shared_graph_functions(self) -> None:
        core = defined_names((METASEED_STATIC_DIR / "js" / "spec-builder-core.js").read_text())
        missing = sorted(set(SHARED_GRAPH_FUNCTIONS) - core)
        assert not missing, f"spec-builder-core.js is missing: {missing}"

    def test_installed_autocomplete_defines_shared_functions(self) -> None:
        widget = defined_names(
            (METASEED_STATIC_DIR / "js" / "ontology-autocomplete.js").read_text()
        )
        missing = sorted(set(SHARED_AUTOCOMPLETE_FUNCTIONS) - widget)
        assert not missing, f"ontology-autocomplete.js is missing: {missing}"


class TestInlineHandlersResolve:
    """Every function an inline handler calls is defined in a loaded script.

    Partials render inside the spec-builder editor page, so their handlers
    resolve against base.html's script chain.
    """

    def _available_names(self, page: Path) -> set[str]:
        names = defined_names(inline_script_text(page))
        for script in loaded_script_paths(page):
            names.update(defined_names(script.read_text()))
        return names

    def test_all_inline_handler_functions_are_defined(self) -> None:
        editor_page = SPEC_BUILDER_TEMPLATES_DIR / "base.html"
        unresolved = []
        for template in spec_builder_templates():
            page = editor_page if template.parent.name == "partials" else template
            available = self._available_names(page)
            for name in sorted(handler_called_names(template.read_text())):
                if name not in available:
                    unresolved.append(f"{template.name}: {name}()")
        assert not unresolved, "\n".join(unresolved)
