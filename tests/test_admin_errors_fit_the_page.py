"""The admin error list fits the page however long an error is.

A database error carries its SQL and parameters -- a thousand characters in
one cell -- and the time column broke into three lines while the table pushed
past its container. The stylesheet had rules for this (``admin-table-scroll``,
``admin-error-text``, ``admin-error-request``) that no template used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.templating import Jinja2Templates

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"
ROW = "admin/_error_row.html"


def _render(message: str) -> str:
    env = Jinja2Templates(directory=str(TEMPLATES)).env
    error = SimpleNamespace(
        occurred_at=datetime(2026, 9, 23, 10, 15, tzinfo=UTC),
        method="POST",
        path="/hub/datasets/5c385f52-8285-47de-be3f-267f00e48f6a/table/b2a862c9/institutions/row",
        exception_type="IntegrityError",
        message=message,
        user=SimpleNamespace(email="reporter@example.org"),
    )
    return env.get_template(ROW).render(e=error)


def test_a_long_message_is_folded_to_its_first_two_hundred_characters() -> None:
    html = _render("x" * 900)

    assert "<details" in html
    assert "x" * 200 in html
    assert ("x" * 201) not in html.split("</summary>")[0]
    assert "x" * 900 in html, "the whole message is still there to open"


def test_a_short_message_is_shown_whole_without_folding() -> None:
    html = _render("duplicate key")

    assert "<details" not in html
    assert "duplicate key" in html


def test_the_cells_carry_the_rules_that_keep_them_in_their_columns() -> None:
    html = _render("short")

    assert 'class="admin-error-when"' in html
    assert 'class="admin-error-request"' in html
    assert 'class="admin-error-text"' in html


def test_the_table_scrolls_rather_than_widening_the_page() -> None:
    dashboard = (TEMPLATES / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="admin-table-scroll"' in dashboard
    assert ROW.split("/")[-1] in dashboard, "the dashboard renders rows through the partial"
