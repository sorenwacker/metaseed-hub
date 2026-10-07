"""The Import page is reachable from the header and its form answers in place.

The requests behind the page are tested without a browser. What only a browser
shows is whether the header link is there to be clicked and whether htmx sends
the form with what the hub requires of it, so the answer appears on the page.
The list sent here is one the hub refuses before fetching anything: no
repository is contacted and no dataset is created.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from metaseed_hub.ui.services.repository_import import MAX_IDENTIFIERS  # noqa: E402
from tests.test_selenium_export import _login, driver  # noqa: F401, E402

pytestmark = pytest.mark.selenium


def _visible(browser, test_id: str):
    return WebDriverWait(browser, 20).until(
        expected_conditions.visibility_of_element_located(
            (By.CSS_SELECTOR, f'[data-testid="{test_id}"]')
        )
    )


def test_the_header_opens_the_import_page_and_the_form_answers(driver) -> None:  # noqa: F811
    _login(driver)

    _visible(driver, "nav-import").click()
    identifiers = _visible(driver, "import-identifiers")
    assert _visible(driver, "import-repository").find_elements(By.TAG_NAME, "option")

    identifiers.send_keys("\n".join(f"PRJEB{n}" for n in range(MAX_IDENTIFIERS + 1)))
    _visible(driver, "btn-import").click()

    assert str(MAX_IDENTIFIERS) in _visible(driver, "import-problem").text
