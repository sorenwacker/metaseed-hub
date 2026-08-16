"""A dataset or spec name must not shape a download header (260817 review).

Two routes built `Content-Disposition: attachment; filename="{name}"` from
names their owners typed. A quote closes the filename and lets the rest read as
header content; a newline splits the header.
"""

from __future__ import annotations

from metaseed_hub.ui.helpers.text import safe_filename


def test_a_quote_cannot_close_the_filename() -> None:
    assert '"' not in safe_filename('evil"; x=1')


def test_a_newline_cannot_split_the_header() -> None:
    cleaned = safe_filename("evil\r\nX-Injected: yes")

    assert "\n" not in cleaned and "\r" not in cleaned


def test_an_ordinary_name_survives() -> None:
    assert safe_filename("wheat-drought_2024") == "wheat-drought_2024"


def test_an_empty_name_yields_the_fallback() -> None:
    assert safe_filename("", fallback="profile") == "profile"


def test_both_download_routes_use_it() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "metaseed_hub"
    for rel in ("ui/routes/dataset/editor.py", "ui/spec_builder/routes/draft_routes.py"):
        assert "safe_filename" in (src / rel).read_text(), rel
