"""User-controlled text must not reach HTML as markup (260817 review).

Two stored-XSS sites of the same class fixed earlier this week in the DCAT
card and the merge report — found again in code those fixes did not touch:

- `delete_draft` interpolates the NAMES of datasets using a spec into an error
  message. A dataset name is whatever its owner typed.
- `dataset_load_example` interpolates `dataset.profile` / `dataset.version`,
  both stored per dataset, into an error message.

Both responses are HTML fragments htmx swaps straight into the page, so the
markup executes for whoever triggers the error.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

HOSTILE = "<img src=x onerror=alert(1)>"
SRC = Path(__file__).resolve().parent.parent / "src" / "metaseed_hub"


def test_the_delete_draft_error_escapes_dataset_names() -> None:
    from metaseed_hub.ui.spec_builder.routes.draft_routes import _dependent_datasets_message

    message = _dependent_datasets_message([HOSTILE])

    assert "<img" not in message, message
    assert escape(HOSTILE) in message


def test_the_example_error_escapes_the_profile_and_version() -> None:
    from metaseed_hub.ui.routes.dataset.crud import _no_example_message

    message = _no_example_message(HOSTILE, "1.0", found=False)

    assert "<img" not in message, message


def test_no_route_module_interpolates_bare_html_error_divs() -> None:
    """A gate, because point fixes keep missing siblings.

    Flags an f-string that drops a value straight inside an error `<div>`
    without escape(). Three separate sites had this shape.
    """
    import re

    pattern = re.compile(
        r"""f["'][^"']*<div[^"']*class=['"]?"""
        r"""(?:notification-)?error[^>]*>\{(?!\s*escape)"""
    )
    offenders = []
    for path in sorted((SRC / "ui").rglob("*.py")):
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{i}")

    assert not offenders, f"unescaped value in an error div: {offenders}"
