"""Creating a specification: choose how to start, then name it.

The page asked for a name first, above the choices, so the natural order --
pick a template, then say what to call the copy -- meant scrolling back up, and
most drafts were created with the field never seen. The version select of a
template listed the oldest version first, so "Use Template" on its default
started from a superseded release.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.conftest import _test_database_url
from tests.factories import make_tenant, make_user


@pytest.fixture
async def page(session) -> str:
    from metaseed_hub.database import db

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
    await session.commit()
    await db.connect(_test_database_url())
    user = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])
    try:
        with patch(
            "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
            AsyncMock(return_value=user),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
            ) as client:
                return (await client.get("/hub/spec-builder/new")).text
    finally:
        await db.disconnect()


def test_the_name_is_asked_in_a_dialog_not_above_the_choices(page: str) -> None:
    assert 'id="name-dialog"' in page
    assert page.index('id="spec-name"') > page.index('id="name-dialog"'), (
        "the name field belongs to the dialog that follows the choice"
    )
    assert page.index('id="name-dialog"') > page.index('id="panel-import"'), (
        "nothing asks for a name before the ways to start"
    )


def test_every_way_to_start_asks_for_the_name(page: str) -> None:
    script = page.split("<script>")[-1]
    for start in ("createEmptySpec", "createFromTemplate", "handleImport"):
        body = script.split(f"function {start}(")[1].split("\nfunction ")[0]
        assert "askName(" in body, f"{start} creates a draft without asking its name"


def test_a_template_offers_its_newest_version_first_and_selected(page: str) -> None:
    select = page.split('id="version-seek-ready-template"')[1].split("</select>")[0]
    versions = re.findall(r'<option value="([^"]+)"( selected)?', select)
    assert len(versions) > 1, "the template under test has several versions"
    numbers = [tuple(int(part) for part in version.split(".")) for version, _ in versions]
    assert numbers == sorted(numbers, reverse=True), "newest first"
    assert [bool(mark) for _, mark in versions] == [True] + [False] * (len(versions) - 1)


def test_the_entity_overview_cells_have_padding() -> None:
    """The table shares ``.data-table`` with the inline-editing grid, whose
    cells are padding-free because an input fills them. These hold text."""
    from pathlib import Path

    css = Path("src/metaseed_hub/ui/static/css/hub.css").read_text()
    rule = css.split(".entity-overview-table td {")[1].split("}")[0]
    assert "padding" in rule
