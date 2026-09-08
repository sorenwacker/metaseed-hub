"""The pages work under the Content-Security-Policy the site actually serves.

The policy is set by nginx, in
``ansible/roles/metaseed-hub/templates/nginx.conf.j2``, and by nothing else:
development, the test client and the rest of the Selenium suite all run against
an application that sends no CSP. So a violation is invisible to every other
test, and three separate ones reached production and stayed there --

* ``hx-headers`` on ``<body>``, which htmx compiles with ``new Function``, so
  every request in the application raised ``EvalError`` and was abandoned;
* ``hx-vals`` on a button, the same;
* eight ``hx-on:`` handlers, also the same -- and the worst of the three,
  because the request succeeded and only the handler afterwards failed. "Add
  Entity" saved the entity and looked completely inert.

``test_templates_need_no_eval.py`` names those three attributes, which stops
them returning. It cannot catch the next thing that needs eval or another
origin. This can: it applies the real policy and asks the browser.

The policy is read from the nginx template rather than copied, so the two cannot
drift; a test asserting a policy the site does not serve proves nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402

from tests.test_selenium_export import BASE, _login, driver  # noqa: F401, E402

pytestmark = pytest.mark.selenium

NGINX = (
    Path(__file__).resolve().parents[1]
    / "ansible"
    / "roles"
    / "metaseed-hub"
    / "templates"
    / "nginx.conf.j2"
)

#: A console entry is a policy violation if it says so. Chrome words a blocked
#: resource and a blocked eval differently, so match both.
VIOLATION = re.compile(
    r"Content Security Policy|violates the following|unsafe-eval|EvalError",
    re.IGNORECASE,
)


def production_csp() -> str:
    """The policy the deployed site sends, read from the nginx template."""
    text = NGINX.read_text()
    match = re.search(r'add_header Content-Security-Policy "([^"]+)"', text)
    assert match, f"no Content-Security-Policy found in {NGINX.name}"
    return match.group(1)


def test_the_nginx_template_still_declares_a_policy() -> None:
    """If the policy moves, this suite must fail rather than quietly pass."""
    policy = production_csp()

    assert "script-src" in policy
    assert "unsafe-eval" not in policy, (
        "the policy now allows eval; that is a deliberate weakening and this "
        "test should be reconsidered rather than updated"
    )


def _violations(browser) -> list[str]:
    return [
        entry["message"]
        for entry in browser.get_log("browser")
        if VIOLATION.search(entry["message"])
    ]


def test_the_spec_builder_raises_no_policy_violation(driver) -> None:  # noqa: F811
    """Driving the builder is what found `hx-on`; the request succeeded and the
    handler after it did not, so only the console said anything was wrong."""
    _login(driver)

    driver.get(f"{BASE}/hub/spec-builder")
    driver.find_element(By.TAG_NAME, "body")

    violations = _violations(driver)
    assert not violations, "the browser refused something the policy forbids:\n" + "\n".join(
        v[:300] for v in violations[:5]
    )
