"""The REST API has a reference page that renders, and the web interface has none.

FastAPI's default Swagger UI page loads its script and stylesheet from a CDN,
which the hub's Content-Security-Policy refuses, so `/docs` answered 200 with a
blank page. The page was also registered for the mounted web interface, where
it published every cookie-authenticated route as though it were an API. See
docs/developer/api.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metaseed_hub.main import create_app

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
VENDORED = PACKAGE / "ui" / "static" / "vendor" / "swagger-ui"
PROFILE_PAGE = PACKAGE / "ui" / "templates" / "profile.html"

LOADED = re.compile(r'(?:src|href)="([^"]+)"')


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_the_page_loads_nothing_from_another_origin(client: TestClient) -> None:
    page = client.get("/docs")
    assert page.status_code == 200

    loaded = LOADED.findall(page.text)
    assert any(url.endswith(".js") for url in loaded), loaded
    assert any(url.endswith(".css") for url in loaded), loaded
    assert [url for url in loaded if not url.startswith("/") or url.startswith("//")] == []


def test_everything_the_page_loads_is_served(client: TestClient) -> None:
    loaded = LOADED.findall(client.get("/docs").text)

    assert {url: client.get(url).status_code for url in loaded} == dict.fromkeys(loaded, 200)


def test_the_vendored_bundle_ships_its_licence_and_version() -> None:
    assert (VENDORED / "LICENSE").is_file()
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", (VENDORED / "VERSION").read_text())


def test_the_page_can_authorize_with_an_access_token(client: TestClient) -> None:
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]

    assert {"type": "http", "scheme": "bearer"} in schemes.values()


def test_redoc_is_not_served(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 404


@pytest.mark.parametrize("path", ["/hub/docs", "/hub/redoc", "/hub/openapi.json"])
def test_the_web_interface_publishes_no_schema(client: TestClient, path: str) -> None:
    assert client.get(path, follow_redirects=False).status_code == 404


def test_the_profile_page_links_the_reference_beside_the_tokens() -> None:
    page = PROFILE_PAGE.read_text()
    tokens = page[page.index("<h2>Access tokens</h2>") : page.index("<h2>SEEK connection</h2>")]

    assert 'href="/docs"' in tokens
    assert "API reference" in tokens


@pytest.mark.parametrize("path", ["/api", "/api/"])
def test_the_api_root_leads_to_the_reference(client: TestClient, path: str) -> None:
    """Reported: the address a person guesses answered Not Found."""
    response = client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"
