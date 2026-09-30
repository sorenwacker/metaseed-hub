"""Every page gets its standard context from one function.

``render_template`` filled the CSRF token and cookie, the version, the
analytics settings and ``base_url``; the spec builder's ``render_with_context``
and the explorer's renderer each re-implemented the list and omitted
``base_url``, so ``base.html`` fell back to its hard-coded production URL on
every spec-builder and explorer page of a local instance. ``standard_context``
in ``ui/render.py`` is the one place now.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import Request
from fastapi.templating import Jinja2Templates

from metaseed_hub.config import get_settings
from metaseed_hub.ui.spec_builder.routes._common import render_with_context

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
RENDER = SRC / "ui" / "render.py"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "cookies": {}})


@pytest.mark.asyncio
async def test_a_spec_builder_page_carries_the_configured_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "probe.html").write_text("{{ base_url }}|{{ version_info is defined }}")
    monkeypatch.setattr(
        "metaseed_hub.ui.spec_builder.routes._common.get_current_user_from_cookie",
        Mock(return_value=None),
        raising=False,
    )

    response = await render_with_context(
        Jinja2Templates(directory=str(tmp_path)), _request(), "probe.html", {"user": None}
    )

    assert response.body.decode() == f"{get_settings().app_url}|True"


def test_only_render_builds_the_standard_context() -> None:
    markers = ('context["version_info"] =', 'context["csrf_token"] =', 'context["base_url"] =')
    offenders = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*.py")
        if path != RENDER
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(marker in line for marker in markers)
    ]
    assert not offenders, "fill the context with standard_context:\n" + "\n".join(offenders)


def test_the_gate_sees_the_builder() -> None:
    source = RENDER.read_text(encoding="utf-8")
    assert "def standard_context(" in source and 'context["base_url"] =' in source
