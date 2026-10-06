"""Names for the records a browser test creates, and their removal afterwards.

The selenium tests run against the developer's own stack, so what they create
lands beside the developer's records. Each run names its records under one
prefix, and the `driver` fixtures delete everything under it when a test ends.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

BASE = os.environ.get("HUB_BASE_URL", "http://localhost:7001")

# Per run, not fixed: two runs against one account must not delete, or fail on,
# each other's records.
RUN_PREFIX = f"selenium-{uuid.uuid4().hex[:6]}-"

_RECORD_URLS = """
const prefix = arguments[0];
const done = arguments[arguments.length - 1];
const page = url => fetch(url, {credentials: 'same-origin'})
    .then(r => r.text())
    .then(html => new DOMParser().parseFromString(html, 'text/html'));
Promise.all([
    page('/hub/?view=table&q=' + encodeURIComponent(prefix)),
    page('/hub/spec-builder'),
]).then(([datasets, drafts]) => done([
    ...[...datasets.querySelectorAll('a.dataset-table-name')]
        .filter(a => a.textContent.trim().startsWith(prefix))
        .map(a => a.getAttribute('href')),
    ...[...drafts.querySelectorAll('[data-testid^="draft-version-' + prefix + '"] [hx-delete]')]
        .map(button => button.getAttribute('hx-delete')),
])).catch(error => done({error: String(error)}));
"""

_DELETE = """
const done = arguments[arguments.length - 1];
const token = document.querySelector('meta[name="csrf-token"]');
fetch(arguments[0], {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': token ? token.content : ''},
}).then(r => done(r.status)).catch(error => done(String(error)));
"""


def record_name(purpose: str) -> str:
    """A unique name for a record this run creates.

    Args:
        purpose: What the record is for, e.g. ``"graph"``.

    Returns:
        A name under the run's prefix, which is how `delete_records` finds it.
    """
    return f"{RUN_PREFIX}{purpose}-{uuid.uuid4().hex[:8]}"


def _record_urls(driver: Any) -> list[str]:
    """Delete URLs of this run's datasets, then its specification drafts.

    Datasets come first: a draft that a dataset is bound to cannot be deleted.
    """
    found = driver.execute_async_script(_RECORD_URLS, RUN_PREFIX)
    assert isinstance(found, list), f"could not list this run's records: {found}"
    return found


def delete_records(driver: Any) -> None:
    """Delete every record of this run that the signed-in account can see.

    Sends the delete requests the pages send, then fails if a record is still
    listed, so a test whose records cannot be removed does not pass quietly.

    Args:
        driver: The browser session the test used.
    """
    driver.get(f"{BASE}/hub/")
    if not driver.current_url.startswith(f"{BASE}/hub/") or "/auth/" in driver.current_url:
        return  # Never signed in, so this session created nothing.

    for url in _record_urls(driver):
        driver.execute_async_script(_DELETE, url)

    left = _record_urls(driver)
    assert not left, f"records the test created were not deleted: {left}"
