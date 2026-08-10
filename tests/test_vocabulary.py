"""The words in the code must name things the hub actually has.

Workspaces were removed from the hub, but the word survived in 61 places —
function names, error messages, admin screens — describing a concept no reader
could find. Someone new to the code cannot tell a live concept from a dead one
by reading it, so the vocabulary needs a gate like any other rule.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Words for concepts the hub no longer has, and what to say instead.
RETIRED_WORDS = {
    "workspace": "account (every account has exactly one tenant)",
    "chat_message": "removed with the chat feature",
}
SEARCHED = ("src/metaseed_hub", "tests")
EXTENSIONS = {".py", ".html", ".md"}
#: Migrations record history and must keep the names the tables had.
EXEMPT = ("alembic/",)


def _files():
    for directory in SEARCHED:
        for path in (ROOT / directory).rglob("*"):
            if path.suffix in EXTENSIONS and path.is_file():
                if not any(part in str(path) for part in EXEMPT):
                    yield path


@pytest.mark.parametrize("word,instead", sorted(RETIRED_WORDS.items()))
def test_a_retired_word_does_not_come_back(word: str, instead: str) -> None:
    offenders = []
    for path in _files():
        if path.name == "test_vocabulary.py":
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if word in line.lower():
                offenders.append(f"{path.relative_to(ROOT)}:{number}")

    assert not offenders, (
        f"'{word}' names a concept the hub no longer has — say {instead}. "
        f"Found in: {', '.join(offenders[:10])}"
    )
