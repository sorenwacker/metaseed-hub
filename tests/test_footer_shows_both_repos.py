"""The footer names which repository each star count belongs to (260817).

It showed one bare "★ N" for the hub. Adding metaseed's count beside it makes
two unlabelled numbers, which say nothing about which project is which — so
each carries its repository name and a hover title.
"""

from __future__ import annotations

from pathlib import Path

BASE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "metaseed_hub"
    / "ui"
    / "templates"
    / "base.html"
).read_text()


def test_both_repositories_are_linked() -> None:
    assert "github.com/sorenwacker/metaseed-hub" in BASE
    assert 'github.com/sorenwacker/metaseed"' in BASE


def test_each_count_is_labelled_with_its_repository() -> None:
    assert "{{ repo_stars }} metaseed-hub" in BASE
    assert "{{ metaseed_stars }} metaseed" in BASE


def test_each_link_has_a_hover_title_naming_the_repository() -> None:
    assert 'title="metaseed-hub on GitHub' in BASE
    assert 'title="metaseed on GitHub' in BASE


def test_the_library_repo_is_a_named_constant() -> None:
    from metaseed_hub.ui.render import METASEED_REPO

    assert METASEED_REPO == "sorenwacker/metaseed"


def test_the_star_getters_take_no_arguments() -> None:
    """The templates call them bare, and tests stub them bare."""
    import inspect

    from metaseed_hub.ui.render import get_metaseed_stars

    assert not inspect.signature(get_metaseed_stars).parameters
