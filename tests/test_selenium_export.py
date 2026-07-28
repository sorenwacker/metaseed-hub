"""Browser E2E: the metaseed adapter export buttons work for a real logged-in user.

The hub auto-deploys to production on a tag and has no other browser coverage, so
this exercises the whole path a user hits: Keycloak login, create a PRIDE dataset
with example data, open it, see the adapter export buttons, and download one.

Marked ``selenium`` and skipped by the default suite. Needs the docker stack
(``make up``), the app on http://localhost:7001, and Chrome.
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
import uuid
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("selenium")
from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402, N812
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

pytestmark = pytest.mark.selenium

_REALM = Path(__file__).resolve().parent.parent / "docker" / "keycloak-realm.json"


def _realm_demo_login(user: str = "demo") -> tuple[str, str]:
    """Return the (username, password) of a realm user, from the imported realm.

    The stack imports ``docker/keycloak-realm.json``; reading the demo login from
    it keeps the test in step with the realm and avoids a credential literal here.
    """
    realm = json.loads(_REALM.read_text())
    for entry in realm.get("users", []):
        if entry.get("username") == user:
            password = next(
                c["value"] for c in entry.get("credentials", []) if c.get("type") == "password"
            )
            return entry["username"], password
    raise LookupError(f"user {user!r} not found in {_REALM}")


BASE = os.environ.get("HUB_BASE_URL", "http://localhost:7001")
# Demo login comes from env (CI injects it); otherwise read it from the realm the
# stack imports, so a local run needs no setup and no credential lives in this file.
_realm_user, _realm_password = _realm_demo_login()
DEMO_USER = os.environ.get("KC_DEMO_USER", _realm_user)
DEMO_PASSWORD = os.environ.get("KC_DEMO_PASSWORD", _realm_password)


@pytest.fixture
def driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--no-sandbox")
    d = webdriver.Chrome(options=opts)
    d.set_page_load_timeout(60)
    try:
        yield d
    finally:
        d.quit()


def _login(driver) -> None:
    driver.get(f"{BASE}/hub/auth/login")
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "username")))
    driver.find_element(By.ID, "username").send_keys(DEMO_USER)
    driver.find_element(By.ID, "password").send_keys(DEMO_PASSWORD)
    driver.find_element(By.ID, "kc-login").click()
    # Back on the hub after the OIDC round trip.
    WebDriverWait(driver, 45).until(lambda d: "/hub" in d.current_url)


def test_pride_adapter_exports_are_usable_end_to_end(driver) -> None:
    _login(driver)

    # Create a PRIDE dataset with the example loaded (so the export has content)
    # by driving the profile-picker UI the way a user does.
    driver.get(f"{BASE}/hub/datasets/new")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "dataset-name")))
    # Unique per run: the tenant enforces unique dataset names.
    driver.find_element(By.ID, "dataset-name").send_keys(f"selenium-export-{uuid.uuid4().hex[:8]}")
    card = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '.standard-card[data-profile="pride"]'))
    )
    # "Create with Example" -> createDatasetWithExample('pride', version)
    example_btn = next(b for b in card.find_elements(By.TAG_NAME, "button") if "Example" in b.text)
    driver.execute_script("arguments[0].click();", example_btn)

    # On the dataset page the adapter export buttons must render.
    WebDriverWait(driver, 45).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="btn-export-pride"]'))
    )
    assert not driver.find_elements(By.CSS_SELECTOR, '[data-testid="btn-export-pride-sdrf"]'), (
        "the SDRF ships inside the single PRIDE submission download, not as its own button"
    )

    # Clicking must produce a real zip. Use the authenticated session cookies to
    # fetch the export URL directly -- robust against headless download handling.
    href = driver.find_element(By.CSS_SELECTOR, '[data-testid="btn-export-pride"]').get_attribute(
        "href"
    )
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in driver.get_cookies())
    req = urllib.request.Request(href, headers={"Cookie": cookie_header})
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "application/zip"
        body = resp.read()
    assert body[:2] == b"PK", "response is not a zip archive"
    # Assert on what the archive holds: the one button must deliver the
    # submission document, not merely a well-formed empty zip.
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert "submission.px" in names, f"archive holds {names}"
