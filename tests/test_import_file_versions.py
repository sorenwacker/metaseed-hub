"""The Import File tab offers the versions of the profile that is chosen.

Each profile option carried its versions as JSON inside a double-quoted
attribute, unescaped, so the attribute ended at the JSON's first quote and held
``[``. The script could not parse it, the version list never followed the
profile, and a file imported under any profile but the first was sent with the
first profile's version and refused.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app

_TOKEN = TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[])


class _ProfileOptions(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inside = False
        self.versions: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = dict(attrs)
        if tag == "select":
            self.inside = given.get("id") == "import-profile"
        elif tag == "option" and self.inside:
            self.versions[str(given.get("value"))] = given.get("data-versions")


async def test_every_profile_option_carries_its_versions(ena_dataset, app_db) -> None:
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_TOKEN),
    ):
        page = TestClient(create_app()).get("/hub/datasets/new")
    options = _ProfileOptions()
    options.feed(page.text)

    assert "1.0" in json.loads(options.versions["ena"] or "")
    assert all(json.loads(versions or "") for versions in options.versions.values())
