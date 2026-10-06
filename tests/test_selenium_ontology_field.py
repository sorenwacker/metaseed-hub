"""An ontology field in a dataset form, in a browser: the Search button opens
the search window, and the stored identifier is explained under the field.

Only a browser shows either: the window is opened by script, and the note is
fetched and written by script. Asks the live ontology service, as the form does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402, N812
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, driver  # noqa: E402, F401
from tests.test_selenium_graph import _dataset_with_example_data  # noqa: E402

pytestmark = pytest.mark.selenium

FIELD = "input.lookup-input[data-lookup-type='ontology']"


def _form_with_an_ontology_field(browser):
    """Open entities of the example dataset until one has an ontology field."""
    dataset_id = _dataset_with_example_data(browser)
    browser.set_window_size(1500, 1000)
    browser.get(f"{BASE}/hub/datasets/{dataset_id}")
    links = WebDriverWait(browser, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, ".entity-overview-table .entity-name")
    )
    for index in range(len(links)):
        browser.find_elements(By.CSS_SELECTOR, ".entity-overview-table .entity-name")[index].click()
        try:
            return WebDriverWait(browser, 5).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, f"#editor {FIELD}"))
            )
        except Exception:
            browser.get(f"{BASE}/hub/datasets/{dataset_id}")
            WebDriverWait(browser, 20).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, ".entity-overview-table .entity-name")
            )
    pytest.fail("the example dataset has no entity with an ontology field")


def _note(browser, field) -> str:
    name = field.get_attribute("id")
    return browser.find_element(By.CSS_SELECTOR, f'[data-term-info-for="{name}"]').text


def test_the_stored_identifier_is_explained(driver) -> None:  # noqa: F811
    field = _form_with_an_ontology_field(driver)

    driver.execute_script(
        "arguments[0].value = 'NCBITaxon:3702';"
        "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
        field,
    )

    WebDriverWait(driver, 30).until(lambda d: "Arabidopsis thaliana" in _note(d, field))


def test_the_search_button_opens_the_search_window(driver) -> None:  # noqa: F811
    field = _form_with_an_ontology_field(driver)
    name = field.get_attribute("id")

    driver.find_element(By.CSS_SELECTOR, f'[data-ontology-search="{name}"]').click()

    WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "ontology-modal")))
