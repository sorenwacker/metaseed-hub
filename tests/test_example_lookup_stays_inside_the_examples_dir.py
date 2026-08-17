"""Example data comes from metaseed's examples directory and nowhere else.

`dataset.profile` and `dataset.version` are free-text form fields at creation:
`dataset_create` accepts any string that does not start with `draft:`/`spec:`.
Both example-loading paths joined them straight onto a base directory —
`examples_dir / profile / version` — so a profile of `../../..` walked out of
the package and any readable `*.yaml` on the server became loadable into a
dataset, where its contents are shown to the user.

Joining is the whole bug: `Path("/base") / "../../etc"` is a perfectly ordinary
path object, and `.exists()` is happy to follow it. The directory is now
resolved and required to sit under the examples root, so an escaping profile
resolves to nothing instead of to somebody else's file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metaseed_hub.ui.routes.dataset.crud import example_version_dir


@pytest.fixture
def examples_root(tmp_path: Path) -> Path:
    root = tmp_path / "examples"
    (root / "miappe" / "1.1").mkdir(parents=True)
    (root / "miappe" / "1.1" / "example.yaml").write_text("investigation: {}\n")
    (tmp_path / "secret.yaml").write_text("password: hunter2\n")
    return root


def test_a_real_profile_and_version_resolve(examples_root: Path) -> None:
    found = example_version_dir(examples_root, "miappe", "1.1")

    assert found == (examples_root / "miappe" / "1.1").resolve()


def test_a_traversing_profile_resolves_to_nothing(examples_root: Path) -> None:
    assert example_version_dir(examples_root, "..", "") is None


def test_a_traversing_version_cannot_reach_a_sibling_file(examples_root: Path) -> None:
    """The escape the review found: out of the package, into any readable dir."""
    assert example_version_dir(examples_root, "miappe", "../../..") is None


def test_an_absolute_profile_does_not_replace_the_root(examples_root: Path) -> None:
    """`Path("/base") / "/etc"` is `/etc` — joining silently discards the base."""
    assert example_version_dir(examples_root, "/etc", "passwd") is None


def test_a_profile_that_does_not_exist_resolves_to_nothing(examples_root: Path) -> None:
    assert example_version_dir(examples_root, "nosuch", "1.0") is None
