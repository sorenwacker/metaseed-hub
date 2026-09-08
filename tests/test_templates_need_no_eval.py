"""No template may use an htmx attribute that requires `eval`.

The production Content-Security-Policy allows `'self'` and `'unsafe-inline'`
for scripts but not `'unsafe-eval'`. htmx evaluates the value of `hx-headers`
and `hx-vals` with `new Function`, so such an attribute raises

    Uncaught EvalError: Evaluating a string as JavaScript violates the
    following Content Security Policy directive

and the request is abandoned. `hx-headers` sat on `<body>`, so it applied to
every request in the application: forms submitted and nothing happened, with the
failure visible only in a browser console on the deployed site.

Nothing caught it because the CSP is set by nginx
(`ansible/roles/metaseed-hub/templates/nginx.conf.j2`), so it exists in
production and nowhere else -- not in development, not in the test client, not
in the Selenium suite, which all serve no CSP at all.

Headers belong in an `htmx:configRequest` listener instead, which needs no eval:
see `static/js/htmx-headers.js`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"

#: htmx attributes whose value it evaluates with `new Function`.
EVAL_ATTRIBUTES = ("hx-headers", "hx-vals")


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def test_there_are_templates_to_check() -> None:
    """A gate over an empty set passes for the wrong reason."""
    assert _templates()


@pytest.mark.parametrize("attribute", EVAL_ATTRIBUTES)
def test_no_template_uses_an_attribute_htmx_evaluates(attribute: str) -> None:
    pattern = re.compile(rf"\b{re.escape(attribute)}\s*=")
    offenders = [
        f"{path.relative_to(TEMPLATES)}:{n}"
        for path in _templates()
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]

    assert not offenders, (
        f"{attribute} is evaluated by htmx with new Function, which the "
        f"production CSP blocks, so the request never happens: {offenders}. "
        "Set the value from an htmx:configRequest listener instead."
    )
