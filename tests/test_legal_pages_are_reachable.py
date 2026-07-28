"""The privacy policy and acceptable use policy must be reachable.

Both pages existed and rendered, but nothing in the interface linked to them —
so a user could not find either without knowing the URL. A privacy policy
nobody can reach is, in practice, a privacy policy nobody has.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from metaseed_hub.main import create_app

TEMPLATES_DIR = Path("src/metaseed_hub/ui/templates")


def test_the_footer_links_to_both_policies() -> None:
    base = (TEMPLATES_DIR / "base.html").read_text()
    footer = base[base.index("hub-footer") :]

    assert '"/hub/privacy"' in footer
    assert '"/hub/aup"' in footer


def test_both_pages_are_served_without_signing_in() -> None:
    """Someone deciding whether to sign in is exactly who needs to read them."""
    with TestClient(create_app()) as client:
        for path in ("/hub/privacy", "/hub/aup"):
            response = client.get(path)
            assert response.status_code == 200, path


def test_the_privacy_policy_names_a_controller_and_the_real_contacts() -> None:
    """The contacts must be TU Delft's real ones, verified against the official
    privacy statement (tudelft.nl/en/privacy-statement). Two fabricated
    addresses shipped originally — a made-up contact in a live privacy policy is
    worse than none."""
    privacy = (TEMPLATES_DIR / "privacy.html").read_text()

    assert "Data Controller" in privacy
    # The verified official addresses.
    assert "privacy-tud@tudelft.nl" in privacy, "GDPR-request contact"
    assert "fg@tudelft.nl" in privacy, "the Data Protection Officer"
    # The fabricated ones must not come back.
    assert "functionarisgegevensbescherming@tudelft.nl" not in privacy
    assert 'mailto:privacy@tudelft.nl"' not in privacy
