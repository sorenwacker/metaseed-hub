"""Creating a specification in a browser: the choice first, then the name."""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402, N812
from selenium.webdriver.support.ui import Select, WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, _login, driver  # noqa: E402, F401

pytestmark = pytest.mark.selenium


def test_a_template_is_chosen_then_named(driver) -> None:  # noqa: F811
    _login(driver)
    driver.get(f"{BASE}/hub/spec-builder/new")
    assert not driver.find_element(By.ID, "spec-name").is_displayed(), (
        "no name is asked before a way to start is chosen"
    )

    driver.find_element(By.CSS_SELECTOR, '.source-tab[data-tab="template"]').click()
    versions = Select(driver.find_element(By.ID, "version-seek-ready-template"))
    newest = max(
        (o.get_attribute("value") for o in versions.options),
        key=lambda v: tuple(int(part) for part in v.split(".")),
    )
    assert versions.first_selected_option.get_attribute("value") == newest
    use = next(
        b
        for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-primary")
        if "createFromTemplate('seek-ready-template')" in (b.get_attribute("onclick") or "")
    )
    driver.execute_script("arguments[0].click();", use)

    name = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "spec-name")))
    chosen = f"selenium-named-{uuid.uuid4().hex[:8]}"
    name.send_keys(chosen)
    driver.find_element(By.CSS_SELECTOR, '[data-testid="name-dialog-create"]').click()

    WebDriverWait(driver, 45).until(
        lambda d: "/hub/spec-builder/" in d.current_url and "/new" not in d.current_url
    )
    assert chosen in driver.page_source
    assert f"seek-ready-template v{newest}" in driver.page_source


def test_cancelling_the_name_creates_nothing(driver) -> None:  # noqa: F811
    _login(driver)
    driver.get(f"{BASE}/hub/spec-builder/new")
    create = next(
        b
        for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-primary")
        if "createEmptySpec" in (b.get_attribute("onclick") or "")
    )
    driver.execute_script("arguments[0].click();", create)
    WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "spec-name")))

    driver.find_element(
        By.CSS_SELECTOR, "#name-dialog .modal-footer .btn:not(.btn-primary)"
    ).click()

    WebDriverWait(driver, 10).until(EC.invisibility_of_element_located((By.ID, "spec-name")))
    assert driver.current_url.endswith("/hub/spec-builder/new")
