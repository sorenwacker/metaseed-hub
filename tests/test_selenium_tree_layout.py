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
_LEVELS = """
const positions = ERD.getNetwork().getPositions();
return [...new Set(Object.values(positions).map(p => Math.round(p.y)))].sort((a, b) => a - b);
"""

#: The distance the tree layout puts between one level and the next.
LEVEL_SEPARATION = 120


def _whole_levels_apart(gaps: list[int]) -> bool:
    """A free arrangement puts entities at arbitrary heights; a tree puts every
    level a whole number of level distances below the one above."""
    return all(min(gap % LEVEL_SEPARATION, -gap % LEVEL_SEPARATION) <= 1 for gap in gaps)


def _press_tree_and_measure_levels(browser) -> list[int]:
    """Press Tree; the distances between the levels the entities then sit on."""
    WebDriverWait(browser, 30).until(lambda d: d.execute_script(_HAS_GRAPH))
    button = WebDriverWait(browser, 20).until(
        expected_conditions.visibility_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="btn-tree-layout"]')
        )
    )
    button.click()
    assert "active" in button.get_attribute("class")
    levels = browser.execute_script(_LEVELS)
    return [lower - upper for upper, lower in zip(levels, levels[1:], strict=False)]


def test_the_builder_s_tree_button_puts_entities_on_evenly_spaced_levels(driver) -> None:  # noqa: F811
    _login(driver)
    driver.set_window_size(1500, 1000)
    _draft_from_the_seek_ready_template(driver)

    gaps = _press_tree_and_measure_levels(driver)

    assert gaps and _whole_levels_apart(gaps), gaps


def test_the_explorer_s_tree_button_puts_entities_on_evenly_spaced_levels(driver) -> None:  # noqa: F811
    _login(driver)
    driver.set_window_size(1500, 1000)
    driver.get(f"{BASE}/hub/explore/")
    Select(driver.find_element(By.ID, "base-profile")).select_by_value("miappe")
    driver.find_element(By.ID, "compare-btn").click()

    gaps = _press_tree_and_measure_levels(driver)

    assert gaps and _whole_levels_apart(gaps), gaps
