"""The Tree button rearranges the graph, in the Builder and in the Explorer.

Where the button is and where the toggle is defined is gated without a browser.
That pressing it moves the nodes is the script's doing against a live graph:
the Builder's button was on the page for a month and did nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions  # noqa: E402
from selenium.webdriver.support.ui import Select, WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, _login, driver  # noqa: E402, F401
from tests.test_selenium_ontology_lookup import (  # noqa: E402
    _draft_from_the_seek_ready_template,
)

pytestmark = pytest.mark.selenium

_HAS_GRAPH = (
    "return Boolean(typeof ERD !== 'undefined' && ERD.getNetwork() && ERD.getNodes().length)"
)
#: Per level (entities sharing a y position), the top of its highest box and
#: the bottom of its lowest, top to bottom.
_LEVEL_EXTENTS = """
const network = ERD.getNetwork();
const levels = new Map();
for (const [id, p] of Object.entries(network.getPositions())) {
    const key = Math.round(p.y);
    const node = ERD.getNodes().get(id) !== null ? id : Number(id);
    const box = network.getBoundingBox(node);
    const extent = levels.get(key) || [Infinity, -Infinity];
    levels.set(key, [Math.min(extent[0], box.top), Math.max(extent[1], box.bottom)]);
}
return [...levels.entries()].sort((a, b) => a[0] - b[0]).map(([, extent]) => extent);
"""


def _press_tree_and_measure_clearances(browser) -> list[float]:
    """Press Tree; the space between each level's lowest box and the next level's highest.

    metaseed spaces tree levels by the size of their boxes (0.64): a level
    starts below the tallest box of the one above, so every clearance is
    positive. A fixed level distance let tall entity boxes overlap, and the
    free arrangement has no levels to speak of.
    """
    WebDriverWait(browser, 30).until(lambda d: d.execute_script(_HAS_GRAPH))
    button = WebDriverWait(browser, 20).until(
        expected_conditions.visibility_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="btn-tree-layout"]')
        )
    )
    button.click()
    assert "active" in button.get_attribute("class")
    extents = browser.execute_script(_LEVEL_EXTENTS)
    return [lower[0] - upper[1] for upper, lower in zip(extents, extents[1:], strict=False)]


def test_the_builder_s_tree_button_puts_entities_on_levels_that_do_not_overlap(driver) -> None:  # noqa: F811
    _login(driver)
    driver.set_window_size(1500, 1000)
    _draft_from_the_seek_ready_template(driver)

    clearances = _press_tree_and_measure_clearances(driver)

    assert clearances and min(clearances) > 0, clearances


def test_the_explorer_s_tree_button_puts_entities_on_levels_that_do_not_overlap(driver) -> None:  # noqa: F811
    _login(driver)
    driver.set_window_size(1500, 1000)
    driver.get(f"{BASE}/hub/explore/")
    Select(driver.find_element(By.ID, "base-profile")).select_by_value("miappe")
    driver.find_element(By.ID, "compare-btn").click()

    clearances = _press_tree_and_measure_clearances(driver)

    assert clearances and min(clearances) > 0, clearances
