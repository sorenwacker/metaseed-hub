"""SEO metadata on the public landing page, and privacy-page accuracy.

The landing page is the only crawlable surface (everything else is behind
login), so it must carry a real description and share preview. The privacy page
must state what is actually collected -- an earlier version claimed a 12-month IP
access log that no code produces, and carried an invented date.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from metaseed_hub.main import create_app

TEMPLATES = Path("src/metaseed_hub/ui/templates")


def test_the_landing_page_has_seo_metadata() -> None:
    base = (TEMPLATES / "base.html").read_text()

    assert 'name="description"' in base
    assert 'property="og:title"' in base
    assert 'property="og:image"' in base
    assert 'rel="canonical"' in base
    # The title distinguishes this Metaseed from its namesakes.
    assert "Scientific Metadata Management" in base


def test_robots_txt_is_served() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/robots.txt")

    assert resp.status_code == 200
    assert "User-agent:" in resp.text
    assert "/hub/matomo/" in resp.text  # tracker kept out of the index


def test_privacy_discloses_analytics() -> None:
    privacy = (TEMPLATES / "privacy.html").read_text()

    assert "Matomo" in privacy
    assert "anonymiz" in privacy.lower(), "IP anonymization must be stated"
    assert "no consent banner" in privacy.lower() or "No cookies" in privacy


def test_privacy_drops_the_fabricated_claims() -> None:
    """The invented 12-month IP access log and the wrong date are gone."""
    privacy = (TEMPLATES / "privacy.html").read_text()

    assert "Retained for 12 months" not in privacy
    assert "April 2026" not in privacy
