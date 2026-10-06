"""The ontology lookup in the spec builder, in a browser.

The suggestions were fetched and built, and nobody saw them: the list is
positioned below its input, outside the form group that holds it, and the
builder's form groups clip their overflow. Only a browser shows that; the
endpoint and the markup were both correct.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("selenium")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402, N812
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from tests.test_selenium_export import BASE, _login, driver  # noqa: E402, F401

pytestmark = pytest.mark.selenium


def _draft_from_the_seek_ready_template(browser) -> None:
    browser.get(f"{BASE}/hub/spec-builder/new")
    form = {
        "name": f"selenium-ontology-{uuid.uuid4().hex[:8]}",
        "template": "seek-ready-template:3.0",
    }
    browser.execute_script(
        "const f=new FormData(); for (const [k,v] of Object.entries(arguments[0])) f.append(k,v);"
        "return fetch('/hub/spec-builder/new',{method:'POST',body:f})"
        ".then(r=>{window.location=r.url})",
        form,
    )
    WebDriverWait(browser, 45).until(
        lambda d: "/hub/spec-builder/" in d.current_url and "/new" not in d.current_url
    )


def _seen_at_its_own_position(browser, element) -> bool:
    """Whether the element is what a click at its centre would hit."""
    return bool(
        browser.execute_script(
            "const r=arguments[0].getBoundingClientRect();"
            "const top=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);"
            "return !!top && arguments[0].contains(top);",
            element,
        )
    )


def test_the_entity_ontology_term_offers_suggestions_that_can_be_chosen(driver) -> None:  # noqa: F811
    _login(driver)
    # Tall enough that the list below the field is inside the viewport: a list
    # scrolled out of view is not the defect under test.
    driver.set_window_size(1500, 1000)
    _draft_from_the_seek_ready_template(driver)
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script("return typeof selectEntity === 'function'")
    )
    driver.execute_script("selectEntity('Sample')")
    field = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, '#editor-content input[name="ontology_term"]')
        )
    )

    field.send_keys("leaf")

    option = WebDriverWait(driver, 30).until(
        lambda d: next(
            iter(d.find_elements(By.CSS_SELECTOR, "#editor-content .ontology-option")), None
        )
    )
    assert _seen_at_its_own_position(driver, option), (
        "the suggestion is built but covered or clipped"
    )

    chosen = option.get_attribute("data-value")
    option.click()

    assert chosen in field.get_attribute("value"), "choosing a suggestion fills the field"
