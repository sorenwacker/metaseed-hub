"""The page a user lands on explains what the hub is for.

Someone signing in for the first time saw an empty "My Datasets" heading and
nothing telling them what a dataset, a specification or the explorer is. The
overview lives on that page rather than in a separate tour, because a separate
tour is the thing people skip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path("src/metaseed_hub/ui/templates")
TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))
# The app registers this global on its own templates instance; the page cannot
# render without it, and stubbing it keeps the test to the page's own content.
TEMPLATES.env.globals["get_repo_stars"] = lambda: 0


def _render(**context) -> str:
    template = TEMPLATES.get_template("home.html")
    return template.render(
        {
            "datasets": [],
            "specs": [],
            "user": None,
            "tenant": None,
            "csrf_token": "t",
            "version_info": {"version": "test"},
            **context,
        }
    )


def test_the_three_parts_are_explained() -> None:
    """Datasets, Specs and Explorer are the hub's whole surface; a newcomer
    needs to know what each is before choosing one."""
    html = _render()

    for part in ("Datasets", "Specs", "Explorer"):
        assert f"<h3>{part}</h3>" in html, f"{part} is not explained"


def test_each_part_links_somewhere_useful() -> None:
    """An explanation with no way to act on it just moves the question along."""
    html = _render()

    assert "/hub/datasets/new" in html
    assert "/hub/spec-builder" in html
    assert "/hub/explore/" in html


def test_it_is_open_for_someone_with_no_work_yet() -> None:
    html = _render(datasets=[], specs=[])

    assert "<details" in html
    assert "open>" in html or " open " in html


def _card(name: str = "Something") -> SimpleNamespace:
    """The attributes the home page reads off a dataset or spec card."""
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000000",
        name=name,
        description=None,
        profile="miappe",
        version="1.1",
        updated_at=datetime(2026, 7, 28, tzinfo=UTC),
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        created_by=None,
        tenant=None,
    )


@pytest.mark.parametrize(
    ("datasets", "specs"),
    [([_card()], []), ([], [_card()]), ([_card()], [_card()])],
)
def test_it_collapses_once_there_is_work_to_show(datasets, specs) -> None:
    """Collapsed, not removed: it stays available as a reference without
    competing with the user's own datasets."""
    html = _render(datasets=datasets, specs=specs)

    assert "<details" in html, "the overview is still on the page"
    overview = html.split("<details")[1].split(">")[0]
    assert "open" not in overview


def test_the_landing_page_explains_the_hub_too() -> None:
    """A visitor deciding whether to sign in needs the same explanation as a
    user deciding what to do first."""
    login = (TEMPLATES_DIR / "login.html").read_text()

    assert 'include "partials/overview.html"' in login


def test_both_pages_share_one_partial() -> None:
    """Two copies of this would drift, and the landing page is the one nobody
    would remember to update."""
    home = (TEMPLATES_DIR / "home.html").read_text()
    login = (TEMPLATES_DIR / "login.html").read_text()

    assert 'include "partials/overview.html"' in home
    # The explanation lives in the partial, not inlined in either page.
    for page in (home, login):
        assert "<h3>Datasets</h3>" not in page
