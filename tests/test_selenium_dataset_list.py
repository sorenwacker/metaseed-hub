"""The dataset list's filters, in a browser.

The filter form told htmx to listen on a `select` inside it. The cards view,
which is the default, renders none, so htmx threw on every load of the home
page; and in the table view the same trigger attached the search to the form's
first input, a hidden field, so typing narrowed nothing. A test that reads the
template cannot see either: both are what htmx does with the attribute.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, _login, driver  # noqa: E402, F401
from tests.test_selenium_graph import (  # noqa: E402
    _dataset_with_example_data,
    _no_severe_console_errors,
)

pytestmark = pytest.mark.selenium


def test_the_cards_view_loads_without_a_script_error(driver) -> None:  # noqa: F811
    _login(driver)
    driver.get(f"{BASE}/hub/?view=cards")
    driver.get_log("browser")  # what the sign-in pages logged is not this page's

    driver.get(f"{BASE}/hub/?view=cards")
    WebDriverWait(driver, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, '[data-testid="dataset-list-controls"]')
    )

    _no_severe_console_errors(driver)


def test_typing_in_the_search_narrows_the_table(driver) -> None:  # noqa: F811
    _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/?view=table")
    rows = '[data-testid="dataset-table"] tbody tr'
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.CSS_SELECTOR, rows))

    search = driver.find_element(By.CSS_SELECTOR, 'input[name="q"]')
    search.send_keys("no-dataset-is-called-this")

    WebDriverWait(driver, 10).until(lambda d: "No datasets match." in d.page_source)
    assert "q=no-dataset-is-called-this" in driver.current_url, "the filter lives in the address"
    _no_severe_console_errors(driver)


def test_choosing_a_profile_narrows_the_table(driver) -> None:  # noqa: F811
    from selenium.webdriver.support.ui import Select

    _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/?view=table")
    WebDriverWait(driver, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, 'select[name="profile"]')
    )

    Select(driver.find_element(By.CSS_SELECTOR, 'select[name="profile"]')).select_by_value("pride")

    WebDriverWait(driver, 10).until(lambda d: "profile=pride" in d.current_url)


def test_choosing_an_access_narrows_the_table(driver) -> None:  # noqa: F811
    """The second select: the trigger reached only the first one in the form."""
    from selenium.webdriver.support.ui import Select

    _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/?view=table")
    WebDriverWait(driver, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, 'select[name="access"]')
    )

    Select(driver.find_element(By.CSS_SELECTOR, 'select[name="access"]')).select_by_value("shared")

    WebDriverWait(driver, 10).until(lambda d: "access=shared" in d.current_url)
