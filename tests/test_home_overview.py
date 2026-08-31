"""What each audience is shown, and where.

Three pages, three jobs, and they had been conflated:

- **Landing (signed out)** — what the hub is for, so a visitor can decide
  whether to sign in. No setup instructions: none of it is actionable yet.
- **Home (signed in)** — how to set up, naming the controls to click. Reached
  from the logo.
- **Datasets** — the dataset list, and nothing else. The overview started as a
  banner here, which pushed a returning user's own work down the page on every
  visit.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("src/metaseed_hub/ui/templates")


def _read(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text()


def test_the_landing_page_says_what_the_hub_is_for() -> None:
    """A visitor cannot act on anything yet, so this is capability, not setup."""
    landing = _read("login.html") + _read("partials/overview.html")

    for capability in (
        "Manage datasets",
        "Build specifications",
        "Explore and compare",
        "Collaborate",
    ):
        assert capability in landing, f"{capability} is not mentioned"


def test_the_landing_page_does_not_give_setup_instructions() -> None:
    """Telling a signed-out visitor to click their profile is useless: they do
    not have one until they sign in."""
    landing = _read("login.html") + _read("partials/overview.html")

    assert "Access tokens" not in landing
    assert "/hub/auth/profile" not in landing


def test_the_signed_in_home_says_where_to_click() -> None:
    """Someone signed in needs the controls named, not the pitch repeated."""
    home = _read("overview_home.html")

    assert "Access tokens" in home, "the API key lives on the profile page"
    assert "/hub/auth/profile" in home
    assert "Sharing" in home
    assert "claude mcp add" in home


def test_the_signed_in_home_links_to_each_area() -> None:
    home = _read("overview_home.html")

    for href in ("/hub/datasets/new", "/hub/spec-builder", "/hub/explore/"):
        assert href in home, f"{href} is not reachable from the setup guide"


def test_the_dataset_list_is_left_alone() -> None:
    """The overview has its own page now, reached from the logo."""
    datasets_page = _read("home.html")

    assert "partials/overview.html" not in datasets_page
    assert "overview" not in datasets_page


def test_the_logo_is_the_home_button() -> None:
    """There is no separate Home nav item; the logo goes there."""
    base = _read("base.html")

    assert 'href="/hub/home" class="hub-logo"' in base
    assert ">Home</a>" not in base, "the logo is the home button, not a nav item"


def test_signed_out_navigation_does_not_lead_nowhere() -> None:
    """Signed out, the Datasets link pointed at ``/hub/`` — which is the landing
    page itself, so clicking it appeared to do nothing. The others bounced to
    sign-in. A visitor gets the sign-in call to action instead."""
    base = _read("base.html")

    nav = base[base.index("hub-nav-global") : base.index("header-breadcrumb")]
    assert "{% if user %}" in nav, "the app nav is only for signed-in users"
    # Docs is public and stays.
    assert "Docs" in nav


def test_both_audiences_can_apply_for_beta_features() -> None:
    """Integrations still in development are rolled out to beta testers. The
    signed-in home says how to apply; a visitor deciding whether to sign in
    could not see it at all. Both pages carry the same registration link, so
    it cannot rot on one of them."""
    registration = (
        "https://sram.surf.nl/registration?collaboration=f4840adc-d5cb-4eb6-9906-ab9827e82df5"
    )
    landing = _read("partials/overview.html")
    home = _read("overview_home.html")
    assert registration in home
    assert registration in landing, "the landing page does not say how to apply"
    assert "beta" in landing.lower()
