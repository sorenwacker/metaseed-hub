"""The middleware's Content-Security-Policy is the one nginx sends in production.

A comment in ``security_headers.py`` named a drift gate; the only test that
read the nginx template was the Selenium one, skipped by the default suite.
This plain comparison is the gate the comment promised.
"""

from __future__ import annotations

import re
from pathlib import Path

from metaseed_hub.security_headers import CONTENT_SECURITY_POLICY

NGINX = (
    Path(__file__).resolve().parents[1]
    / "ansible"
    / "roles"
    / "metaseed-hub"
    / "templates"
    / "nginx.conf.j2"
)


def test_the_two_policies_are_identical() -> None:
    match = re.search(r'add_header Content-Security-Policy "([^"]+)"', NGINX.read_text())
    assert match, f"no Content-Security-Policy in {NGINX.name}"
    assert match.group(1) == CONTENT_SECURITY_POLICY


def test_the_comment_names_this_gate() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src/metaseed_hub/security_headers.py"
    ).read_text()
    assert "test_the_csp_matches_production.py" in source
