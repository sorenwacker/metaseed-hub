"""Every cookie this app sets is marked secure outside debug.

`auth.py` writes four cookies. Three pass `secure=not settings.debug`; the
one-shot cookie carrying a freshly minted personal access token did not, and
it is the one holding a live credential — for sixty seconds a plain-HTTP
request would send that token in clear.

The gate is written over the source because the omission is a missing
argument: no response object can show the difference between "secure=False"
and "the writer forgot", and it is the forgetting that recurs.
"""

from __future__ import annotations

import ast
from pathlib import Path

_AUTH = Path("src/metaseed_hub/ui/routes/auth.py")


def _set_cookie_calls() -> list[ast.Call]:
    tree = ast.parse(_AUTH.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_cookie"
    ]


def test_every_cookie_writer_sets_the_secure_flag() -> None:
    calls = _set_cookie_calls()

    assert calls, "no set_cookie calls found; this gate would pass vacuously"

    missing = [
        ast.unparse(call.args[0]) if call.args else ast.unparse(call)
        for call in calls
        if "secure" not in {keyword.arg for keyword in call.keywords}
    ]

    assert missing == [], f"cookies written without the secure flag: {missing}"
