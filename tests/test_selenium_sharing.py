"""Browser E2E: a spec draft can actually be shared with another person.

The Sharing tab reported "User not found. They must log in first before you can
share." at collaborators who were already signed in, because the invitee lookup
was scoped to the sharer's tenant and every account has a tenant of its own. The
route-level tests cover the lookup; this covers the thing the user does -- type a
colleague's address into the Sharing tab and see them appear in the member list.

Marked ``selenium`` and skipped by the default suite. Needs the docker stack
(``make up``), the app on http://localhost:7001, and Chrome.
"""

from __future__ import annotations

import json
import os
import uuid
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
BASE = os.environ.get("HUB_BASE_URL", "http://localhost:7001")


def _realm_user(username: str) -> tuple[str, str, str]:
    """Return (username, password, email) for a realm user.

    Read from the realm the stack imports so the test stays in step with it and
    no credential literal lives in this file.

    Args:
        username: The realm username to look up.

    Returns:
        The username, its password, and its email address.
    """
    realm = json.loads(_REALM.read_text())
    for entry in realm.get("users", []):
        if entry.get("username") == username:
            password = next(
                c["value"] for c in entry.get("credentials", []) if c.get("type") == "password"
            )
            return entry["username"], password, entry["email"]
    raise LookupError(f"user {username!r} not found in {_REALM}")


def _driver() -> webdriver.Chrome:
    """A headless Chrome sized for the spec-builder layout."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


@pytest.fixture
def driver():
    """The sharer's browser session."""
    d = _driver()
    try:
        yield d
    finally:
        d.quit()


def _login(driver, username: str, password: str) -> None:
    """Sign in through Keycloak and return to the hub."""
    driver.get(f"{BASE}/hub/auth/login")
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "username")))
    driver.find_element(By.ID, "username").send_keys(username)
    driver.find_element(By.ID, "password").send_keys(password)
    driver.find_element(By.ID, "kc-login").click()
    WebDriverWait(driver, 45).until(lambda d: "/hub" in d.current_url)


def _ensure_account_exists(username: str, password: str) -> None:
    """Sign the invitee in once, because sharing only resolves existing accounts.

    An account row is created lazily on the first authenticated page, so a
    collaborator who has never opened the hub cannot be shared with.
    """
    d = _driver()
    try:
        _login(d, username, password)
    finally:
        d.quit()


def test_a_spec_draft_can_be_shared_with_a_colleague(driver) -> None:
    demo_user, demo_password, _ = _realm_user("demo")
    invitee_user, invitee_password, invitee_email = _realm_user("admin")
    _ensure_account_exists(invitee_user, invitee_password)

    _login(driver, demo_user, demo_password)

    # Create a draft to share. Draft names are unique per user.
    driver.get(f"{BASE}/hub/spec-builder/new")
    name_field = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, "spec-name"))
    )
    name_field.send_keys(f"selenium-sharing-{uuid.uuid4().hex[:8]}")
    create = next(
        b
        for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-primary")
        if "createEmptySpec" in (b.get_attribute("onclick") or "")
    )
    driver.execute_script("arguments[0].click();", create)
    WebDriverWait(driver, 45).until(
        lambda d: "/hub/spec-builder/" in d.current_url and "/new" not in d.current_url
    )

    # Open the Sharing tab and add the colleague by the address on their profile.
    sharing_tab = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.sidebar-tab[data-tab="sharing"]'))
    )
    driver.execute_script("arguments[0].click();", sharing_tab)
    email_field = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="share-form"] input[name="email"]')
        )
    )
    # Typed in a casing of the sharer's choosing, as a person would.
    email_field.send_keys(invitee_email.upper())
    driver.find_element(By.CSS_SELECTOR, "[data-testid='share-form'] button[type='submit']").click()

    # The collaborator must appear in the member list, not a "log in first" toast.
    WebDriverWait(driver, 20).until(
        lambda d: invitee_email.split("@")[0] in d.find_element(By.ID, "member-list").text.lower()
    )
    member_list = driver.find_element(By.ID, "member-list").text
    assert "log in" not in member_list.lower(), member_list
