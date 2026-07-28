"""Matomo analytics: opt-in by configuration, cookieless, same-origin.

Analytics render only when a site id is set, so dev and CI emit nothing. When
configured, the snippet must disable cookies -- that is what keeps it exempt
from the consent-banner requirement under GDPR/ePrivacy -- and load first-party
so the strict CSP (script-src/connect-src 'self') allows it.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

TEMPLATES = Jinja2Templates(directory="src/metaseed_hub/ui/templates")
TEMPLATES.env.globals["get_repo_stars"] = lambda: 0


def _render(**ctx) -> str:
    base = {
        "user": None,
        "csrf_token": "t",
        "version_info": {"version": "test"},
        "matomo_url": "/matomo/",
        "matomo_site_id": "",
    }
    base.update(ctx)
    return TEMPLATES.get_template("base.html").render(base)


def test_nothing_is_emitted_when_unconfigured() -> None:
    """No site id -> no tracking, no third-party request, nothing to consent to."""
    html = _render(matomo_site_id="")

    assert "matomo.js" not in html
    assert "_paq" not in html


def test_the_tracker_is_emitted_when_a_site_id_is_set() -> None:
    html = _render(matomo_site_id="1")

    assert "matomo.js" in html
    assert "setSiteId" in html
    assert "'1'" in html


def test_the_tracker_is_cookieless() -> None:
    """disableCookies is what removes the consent-banner obligation."""
    html = _render(matomo_site_id="1")

    assert "disableCookies" in html


def test_the_tracker_is_first_party() -> None:
    """Same-origin, so the strict CSP allows it and Safari does not block it."""
    html = _render(matomo_site_id="1", matomo_url="/matomo/")

    assert "/matomo/" in html
    # No absolute third-party origin in the tracker.
    tracker = html[html.index("_paq") :]
    assert "http://" not in tracker
    assert "https://" not in tracker.split("</script>")[0]


def test_a_missing_context_variable_does_not_break_the_page() -> None:
    """Any render path that forgets to pass the vars must still produce a page,
    just without analytics -- not a template error."""
    html = TEMPLATES.get_template("base.html").render(
        {"user": None, "csrf_token": "t", "version_info": {"version": "test"}}
    )

    assert "<body" in html
    assert "matomo.js" not in html
