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


#: What ENA calls PRJDA51199 today; the import names the dataset after it. A
#: soft-deleted dataset keeps its name, so the rows this test leaves are
#: removed outright, before and after, or the next run finds both names taken.
ENA_TITLE = "Arabidopsis thaliana tonagata-0004 transcriptome project"


def _remove_outright(*, names: tuple[str, ...] = (), ids: tuple[str, ...] = ()) -> int:
    """Hard-delete the datasets this test makes; every table that refers to
    a dataset cascades. Returns how many rows went."""
    import asyncio

    from sqlalchemy import delete, or_
    from sqlalchemy.ext.asyncio import create_async_engine

    from metaseed_hub.config import get_settings
    from metaseed_hub.models import Dataset

    async def _run() -> int:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(
                    delete(Dataset).where(or_(Dataset.name.in_(names), Dataset.id.in_(ids)))
                )
                return int(result.rowcount or 0)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_an_import_runs_as_a_job_the_panel_follows(driver) -> None:  # noqa: F811
    """One real ENA study (four runs), end to end: the panel polls the job, the bar fills,
    the dataset and the end of the job are announced."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    _remove_outright(names=(ENA_TITLE, f"{ENA_TITLE} (PRJDA51199)"))
    _login(driver)
    driver.get(f"{BASE}/hub/datasets/new")
    _visible(driver, "tab-repository").click()
    _visible(driver, "import-identifiers").send_keys("PRJDA51199")
    # The tab also lists the person's earlier jobs, each with a panel of its
    # own, so the new one is told apart by not having been there before.
    before = {
        panel.get_attribute("id")
        for panel in driver.find_elements(By.CSS_SELECTOR, '[data-testid="import-job"]')
    }
    _visible(driver, "btn-import-ena").click()

    panel_id = WebDriverWait(driver, 20).until(
        lambda d: next(
            (
                panel.get_attribute("id")
                for panel in d.find_elements(By.CSS_SELECTOR, '[data-testid="import-job"]')
                if panel.get_attribute("id") not in before
            ),
            None,
        )
    )
    # A job that ENA answers at once is done before the panel can be read, so
    # nothing is asserted about the running state; the end is what matters.
    WebDriverWait(driver, 180).until(
        lambda d: d.find_element(By.ID, panel_id).get_attribute("data-status") != "running"
    )
    panel = driver.find_element(By.ID, panel_id)
    row = panel.find_element(By.CSS_SELECTOR, '[data-testid="import-row"]')
    assert row.get_attribute("data-status") == "imported", row.text
    link = row.find_element(By.CSS_SELECTOR, '[data-testid="import-dataset-link"]')
    try:
        toasts = [
            toast.text
            for toast in driver.find_elements(
                By.CSS_SELECTOR, "#notification-container .notification-success"
            )
        ]
        assert f"Imported {link.text}" in toasts
        assert "Import finished: 1 imported" in toasts
        bar = panel.find_element(By.CSS_SELECTOR, '[data-testid="import-progress"]')
        assert bar.get_attribute("value") == "1"
        label = panel.find_element(By.CSS_SELECTOR, '[data-testid="import-progress-label"]')
        assert label.text.startswith("1 of 1 done, finished")
        assert "hx-trigger" not in panel.get_attribute("outerHTML")
    finally:
        assert _remove_outright(ids=(link.get_attribute("href").rsplit("/", 1)[1],)) == 1
