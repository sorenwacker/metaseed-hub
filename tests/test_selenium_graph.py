"""The graph page draws, using metaseed's drawing rather than its own copy.

`graph.html` used to inline ~280 lines of vis.js drawing — a lesser copy of
`metaseed/ui/static/js/graph.js`. Deleting it and loading the library instead
is invisible to every static check: a template can name the right script and
set the right URL while the page renders nothing at all.

So this drives the real page. It creates its own dataset with example data,
because a graph of nothing proves nothing, and asserts the canvas and the
legend that only the library produces.
"""

from __future__ import annotations

import uuid

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC  # noqa: N812
from selenium.webdriver.support.ui import WebDriverWait

from tests.test_selenium_export import BASE, _login, driver  # noqa: F401

pytestmark = pytest.mark.selenium


def _dataset_with_example_data(browser) -> str:
    """A fresh pride dataset filled from the example, its id. A graph of
    nothing proves nothing."""
    _login(browser)

    browser.get(f"{BASE}/hub/datasets/new")
    WebDriverWait(browser, 20).until(EC.presence_of_element_located((By.ID, "dataset-name")))
    browser.find_element(By.ID, "dataset-name").send_keys(f"selenium-graph-{uuid.uuid4().hex[:8]}")
    card = WebDriverWait(browser, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '.standard-card[data-profile="pride"]'))
    )
    example = next(b for b in card.find_elements(By.TAG_NAME, "button") if "Example" in b.text)
    browser.execute_script("arguments[0].click();", example)

    WebDriverWait(browser, 45).until(
        lambda d: "/hub/datasets/" in d.current_url and "/new" not in d.current_url
    )
    return browser.current_url.rstrip("/").split("/hub/datasets/")[1].split("/")[0]


def _no_severe_console_errors(browser) -> None:
    severe = [entry for entry in browser.get_log("browser") if entry["level"] == "SEVERE"]
    assert not [e for e in severe if "favicon" not in e["message"]], severe


def test_the_graph_opens_beside_the_editor_on_the_dataset_page(driver) -> None:  # noqa: F811
    """Reported: the graph could not be seen at the same time as the entity
    table. The sidebar button opens it next to the editor, drawn by the
    library, and the editor stays on screen.

    The button id matters: graph.js treats `view-graph-btn` as its own view
    switch, whose polling lives in a file the hub does not load. The first
    version of this page used that id and threw on every load.
    """
    dataset_id = _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/datasets/{dataset_id}")
    WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "dataset-graph-btn")))
    assert not driver.find_element(By.ID, "graph-container").is_displayed(), "closed on arrival"

    driver.find_element(By.ID, "dataset-graph-btn").click()

    WebDriverWait(driver, 45).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "#graph-container #graph-view canvas")
    )
    assert driver.find_element(By.ID, "graph-container").is_displayed()
    assert driver.find_element(By.ID, "editor").is_displayed(), "the editor must stay in view"
    assert driver.find_element(By.ID, "dataset-graph-btn").get_attribute("aria-pressed") == "true"
    WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.ID, "graph-legend").text.strip() != ""
    )

    # Reopening the dataset brings the graph back the way it was left.
    driver.get(f"{BASE}/hub/datasets/{dataset_id}")
    WebDriverWait(driver, 45).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "#graph-container #graph-view canvas")
    )
    _no_severe_console_errors(driver)


def test_the_graph_page_draws_the_dataset(driver) -> None:  # noqa: F811
    dataset_id = _dataset_with_example_data(driver)

    driver.get(f"{BASE}/hub/datasets/{dataset_id}/graph")

    # The canvas is vis.js drawing; its absence is the whole failure mode a
    # template-scanning test cannot see.
    WebDriverWait(driver, 45).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "#graph-view canvas")
    )

    # The legend with per-entity-type counts is the library's, and is one of the
    # things the hub's copy never had.
    WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.ID, "graph-legend").text.strip() != ""
    )
    legend = driver.find_element(By.ID, "graph-legend").text
    assert any(char.isdigit() for char in legend), (
        f"the legend must carry per-entity-type counts, got: {legend!r}"
    )

    severe = [entry for entry in driver.get_log("browser") if entry["level"] == "SEVERE"]
    assert not [e for e in severe if "favicon" not in e["message"]], severe
