"""``base_url`` is the mount prefix; the instance's origin is ``app_url``.

Release 0.59.0 routed every page through ``standard_context`` and filled
``base_url`` with ``settings.app_url``, the absolute origin. The templates use
``base_url`` as the prefix in front of a route, so the Explorer posted its
comparison to ``https://metaseed.ewi.tudelft.nl/explore/compare`` -- outside
the ``/hub`` mount, a 404 -- on every click of **Compare** during a demo.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.routing import Mount

from metaseed_hub.auth import TokenUser
from metaseed_hub.config import MOUNT_PREFIX, get_settings
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import get_current_user_from_cookie
from metaseed_hub.ui.render import standard_context

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"
_TOKEN = TokenUser(sub="kc-explorer", email="e@example.org", name="E", roles=[], entitlements=[])


def test_the_standard_context_keeps_the_prefix_and_the_origin_apart() -> None:
    context: dict = {}
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "cookies": {}})

    standard_context(request, context)

    assert context["base_url"] == MOUNT_PREFIX == "/hub"
    assert context["app_url"] == get_settings().app_url
    assert not context["app_url"].endswith("/hub")


@pytest.mark.asyncio
async def test_the_explorer_compares_under_the_mount(session) -> None:
    """The page's own fetch URL must be a route the app serves."""
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: _TOKEN
    with (
        patch(
            "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
            AsyncMock(return_value=_TOKEN),
        ),
        TestClient(app, base_url="https://test") as client,
    ):
        page = client.get("/hub/explore/")
        assert page.status_code == 200, page.text[:300]
        (base_url,) = re.findall(r"const BASE_URL = '([^']*)'", page.text)
        assert base_url == "/hub"

        answer = client.post(f"{base_url}/explore/compare", json={"profiles": []})

    assert answer.status_code != 404, "the Explorer posts to a path the app does not serve"


def test_only_the_page_identity_uses_the_origin() -> None:
    """The canonical link and the share preview name the instance; nothing else
    in a template may take the origin, or a route gets built on it again."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "app_url" in base
    assert "base_url" not in base
    offenders = [
        str(path.relative_to(TEMPLATES))
        for path in TEMPLATES.rglob("*.html")
        if path.name != "base.html" and "app_url" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
