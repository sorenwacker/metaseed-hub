"""The entity overview folds and unfolds in a browser.

Which rows are in the page is tested without one. Whether a folded row is out
of sight and a click brings it back is the script's doing, and only a browser
runs it. See docs/datasets/entities.md, *The entity overview*.
"""

from __future__ import annotations

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, driver  # noqa: E402, F401
from tests.test_selenium_graph import _dataset_with_example_data  # noqa: E402

pytestmark = pytest.mark.selenium

ROWS = ".entity-overview-table tbody tr[data-node]"


def _rows(browser) -> list:
    return browser.find_elements(By.CSS_SELECTOR, ROWS)


def _listed(browser) -> list[str]:
    return [row.get_attribute("data-node") for row in _rows(browser) if row.is_displayed()]


def _click(browser, test_id: str) -> None:
    browser.find_element(By.CSS_SELECTOR, f'[data-testid="{test_id}"]').click()


def test_levels_fold_and_unfold(driver) -> None:  # noqa: F811
    dataset_id = _dataset_with_example_data(driver)
    driver.get(f"{BASE}/hub/datasets/{dataset_id}")
    WebDriverWait(driver, 20).until(lambda d: _listed(d))

    every = [row.get_attribute("data-node") for row in _rows(driver)]
    roots = [
        row.get_attribute("data-node")
        for row in _rows(driver)
        if not row.get_attribute("data-parent")
    ]
    on_arrival = _listed(driver)
    assert set(roots) < set(on_arrival), "the direct children of the roots are listed"

    _click(driver, "overview-collapse-all")
    assert _listed(driver) == roots

    _click(driver, "overview-expand-all")
    assert _listed(driver) == every

    # One toggle acts on one level: folding the first root hides what is below
    # it and leaves the other roots' rows alone.
    first_root = driver.find_element(By.CSS_SELECTOR, f'{ROWS}[data-node="{roots[0]}"]')
    first_root.find_element(By.CSS_SELECTOR, '[data-testid="overview-toggle"]').click()
    below_first = {
        row.get_attribute("data-node")
        for row in _rows(driver)
        if row.get_attribute("data-parent") == roots[0]
    }
    assert below_first and not below_first & set(_listed(driver))


def _study_file(tmp_path, samples: int):
    """The shipped ENA example with ``samples`` samples, as a file to upload."""
    import copy
    from pathlib import Path

    import metaseed
    import yaml

    example = next((Path(metaseed.__file__).parent / "examples" / "ena" / "1.0").glob("*.yaml"))
    study = yaml.safe_load(example.read_text())
    first = study["samples"][0]
    study["samples"] = [
        {**copy.deepcopy(first), "alias": f"sample-{n:03d}"} for n in range(samples)
    ]
    for emptied in ("experiments", "analyses"):
        study[emptied] = []
    path = tmp_path / "many-samples.yaml"
    path.write_text(yaml.safe_dump(study))
    return path


def test_a_long_list_shows_a_batch_and_then_more(driver, tmp_path) -> None:  # noqa: F811
    from selenium.webdriver.support.ui import Select

    from metaseed_hub.ui.routes.dataset.editor import OVERVIEW_BATCH
    from tests.selenium_records import record_name
    from tests.test_selenium_export import _login

    samples = OVERVIEW_BATCH + 20
    _login(driver)
    driver.get(f"{BASE}/hub/datasets/new")
    WebDriverWait(driver, 20).until(lambda d: d.find_element(By.ID, "dataset-name").is_displayed())
    driver.find_element(By.ID, "dataset-name").send_keys(record_name("overview"))
    driver.find_element(By.CSS_SELECTOR, '.source-tab[data-tab="import"]').click()
    WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.ID, "import-profile").is_displayed()
    )
    Select(driver.find_element(By.ID, "import-profile")).select_by_value("ena")
    Select(driver.find_element(By.ID, "import-version")).select_by_value("1.0")
    driver.find_element(By.ID, "import-file").send_keys(str(_study_file(tmp_path, samples)))
    WebDriverWait(driver, 45).until(
        lambda d: "/hub/datasets/" in d.current_url and "/new" not in d.current_url
    )
    WebDriverWait(driver, 20).until(lambda d: _listed(d))

    # The study's children are its samples and a few records of other types;
    # the batch counts them all, in tree order.
    study = next(
        row.get_attribute("data-node")
        for row in _rows(driver)
        if not row.get_attribute("data-parent")
    )
    children = [row for row in _rows(driver) if row.get_attribute("data-parent") == study]
    assert len(children) > samples

    def listed_children() -> int:
        return sum(1 for row in children if row.is_displayed())

    assert listed_children() == OVERVIEW_BATCH
    more = driver.find_element(By.CSS_SELECTOR, '[data-testid="overview-more"]')
    assert f"{len(children) - OVERVIEW_BATCH} not listed" in more.find_element(By.XPATH, "..").text

    more.click()

    assert listed_children() == len(children)
    assert not more.is_displayed(), "nothing is left to show"
