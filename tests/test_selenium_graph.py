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


def test_the_graph_page_draws_the_dataset(driver) -> None:  # noqa: F811
    _login(driver)

    driver.get(f"{BASE}/hub/datasets/new")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "dataset-name")))
    driver.find_element(By.ID, "dataset-name").send_keys(f"selenium-graph-{uuid.uuid4().hex[:8]}")
    card = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '.standard-card[data-profile="pride"]'))
    )
    example = next(b for b in card.find_elements(By.TAG_NAME, "button") if "Example" in b.text)
    driver.execute_script("arguments[0].click();", example)

    WebDriverWait(driver, 45).until(
        lambda d: "/hub/datasets/" in d.current_url and "/new" not in d.current_url
    )
    dataset_id = driver.current_url.rstrip("/").split("/hub/datasets/")[1].split("/")[0]

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
