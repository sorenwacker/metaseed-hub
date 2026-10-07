"""The YAML download is offered on the dataset page and answers the signed-in browser."""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, driver  # noqa: E402, F401
from tests.test_selenium_graph import _dataset_with_example_data  # noqa: E402

pytestmark = pytest.mark.selenium


def test_the_sidebar_button_downloads_the_dataset_as_yaml(driver) -> None:  # noqa: F811
    dataset_id = _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/datasets/{dataset_id}")

    button = WebDriverWait(driver, 20).until(
        expected_conditions.visibility_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="btn-export-yaml"]')
        )
    )
    text = driver.execute_async_script(
        "const done = arguments[arguments.length - 1];"
        "fetch(arguments[0]).then(r => r.text()).then(done);",
        button.get_attribute("href"),
    )

    assert text.startswith("profile: pride\n")
    assert "entities:" in text
