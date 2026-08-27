"""Every log record the hub writes stays on one line.

A request value -- a dataset name, a field value -- logged verbatim with a
line break in it would start a second line that reads as a record of its own.
The formatter metaseed ships strips the breaks in the one place every record
passes; the hub has to actually be using it on the handler it installs.
(Under pytest the root logger already carries the capture handlers, so the
test looks at the hub's own handler rather than at the root.)
"""

from __future__ import annotations

import io
import logging

from metaseed.logging import OneLineFormatter


def test_the_hubs_handler_keeps_records_on_one_line() -> None:
    from metaseed_hub.ui.app import _log_handler

    assert isinstance(_log_handler.formatter, OneLineFormatter)
    record = logging.LogRecord(
        "metaseed_hub.test",
        logging.INFO,
        __file__,
        1,
        "saved %s",
        ("test-name\nERROR forged\rmore",),
        None,
    )
    text = _log_handler.formatter.format(record)
    assert "\n" not in text and "\r" not in text
    assert "forged" in text


def test_a_message_through_the_hubs_formatter_is_one_line() -> None:
    from metaseed_hub.ui.app import _log_handler

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_log_handler.formatter)
    log = logging.getLogger("metaseed_hub.one_line_test")
    log.setLevel(logging.INFO)  # pytest leaves the tree at WARNING
    log.addHandler(handler)
    try:
        log.info("value %s", "a\nb")
    finally:
        log.removeHandler(handler)
    assert stream.getvalue().count("\n") == 1
