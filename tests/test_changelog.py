"""Every released version says what it changed.

The hub's changelog stopped at 0.30.3 while eight further versions shipped:
0.31.0 through 0.32.0, including a security fix. Unlike metaseed, the hub tags
a release without any gate asking for an entry, so the omission was invisible —
and the record of what a running version contains was simply absent.

This checks the file against the tags that exist. It does not need the network:
a tag is in the repository or it is not.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
#: Versions released before this gate existed and never written up. Empty, and
#: to be kept that way: an entry is written with the change, not afterwards.
UNDOCUMENTED: frozenset[str] = frozenset()


def _released_versions() -> set[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"], capture_output=True, text=True, check=False
    )
    return {
        line.strip().lstrip("v")
        for line in result.stdout.splitlines()
        if re.fullmatch(r"v\d+\.\d+\.\d+", line.strip())
    }


def _documented_versions() -> set[str]:
    return set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(), re.M))


def test_every_released_version_has_an_entry() -> None:
    missing = _released_versions() - _documented_versions() - UNDOCUMENTED

    assert not missing, (
        "these versions are tagged but say nothing about what they changed: "
        + ", ".join(sorted(missing, key=lambda v: [int(p) for p in v.split(".")]))
    )


def test_no_version_is_written_up_twice() -> None:
    """A duplicated heading means two accounts of one version, and a reader
    cannot tell which is true."""
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(), re.M)

    duplicates = {v for v in headings if headings.count(v) > 1}
    assert not duplicates, f"written up more than once: {sorted(duplicates)}"


def test_the_entries_are_in_order() -> None:
    """Newest first, so the top of the file is the current state."""
    versions = [
        [int(part) for part in v.split(".")]
        for v in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(), re.M)
    ]

    assert versions == sorted(versions, reverse=True), "the changelog is not newest-first"
