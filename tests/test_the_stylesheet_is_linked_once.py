"""The stylesheet is linked once, from the base template, with a version.

The explorer template linked metaseed's stylesheet a second time without a
version query, after the versioned link every page inherits. The unversioned
copy is what a browser caches, and being later in the document it wins the
cascade, so the explorer kept the colours from before a restyle.
"""

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent.parent / "src/metaseed_hub/ui/templates"


def test_only_the_base_template_links_the_stylesheet_and_it_is_versioned() -> None:
    links: dict[str, list[str]] = {}
    for template in TEMPLATE_DIR.rglob("*.html"):
        found = re.findall(r'href="([^"]*style\.css[^"]*)"', template.read_text())
        if found:
            links[str(template.relative_to(TEMPLATE_DIR))] = found
    assert list(links) == ["base.html"], links
    assert all("?v=" in link for link in links["base.html"]), links
