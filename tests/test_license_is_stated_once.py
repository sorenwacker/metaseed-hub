"""The license is MIT, and every place that names it agrees.

The hub said Apache 2.0 while metaseed said MIT, for no recorded reason;
the LICENSE file had been added to match a badge. Three places name the
license -- the file, the README, and the package metadata -- and they drift
independently unless something holds them together.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_license_file_is_mit() -> None:
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in text


def test_the_readme_names_the_same_license() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "license-MIT" in readme, "the badge"
    assert re.search(r"^\[MIT\]\(LICENSE\)$", readme, re.M), "the License section"
    assert "Apache" not in readme


def test_the_package_metadata_names_the_same_license() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"]["text"] == "MIT"
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]
    assert not any("Apache" in c for c in project["classifiers"])
