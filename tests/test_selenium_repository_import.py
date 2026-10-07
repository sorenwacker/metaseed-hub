"""The repository tab of the New Dataset screen answers in place.

The requests behind the tab are tested without a browser. What only a browser
shows is whether the tab opens and whether htmx sends the form, with the
button that was pressed, so the answer appears on the page. The list sent here
is one the hub refuses before fetching anything: no repository is contacted
and no dataset is created.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from metaseed_hub.ui.services.repository_import import MAX_IDENTIFIERS  # noqa: E402
from tests.test_selenium_export import BASE, _login, driver  # noqa: F401, E402

pytestmark = pytest.mark.selenium


def _visible(browser, test_id: str):
    return WebDriverWait(browser, 20).until(
        expected_conditions.visibility_of_element_located(
            (By.CSS_SELECTOR, f'[data-testid="{test_id}"]')
        )
    )


def test_the_tab_opens_and_a_repository_button_sends_the_list(driver) -> None:  # noqa: F811
    _login(driver)
    driver.get(f"{BASE}/hub/datasets/new")

    _visible(driver, "tab-repository").click()
    identifiers = _visible(driver, "import-identifiers")
    identifiers.send_keys("\n".join(f"PRJEB{n}" for n in range(MAX_IDENTIFIERS + 1)))
    _visible(driver, "btn-import-ena").click()

    assert str(MAX_IDENTIFIERS) in _visible(driver, "import-problem").text
