"""An override must have the signature it overrides (260817 review).

`HubSpecLoader.load_profile` accepted a keyword-only `ctx` and forwarded it to
`SpecLoader.load_profile`, which takes no such parameter — so every
fall-through to a built-in profile raised TypeError and /explore was broken for
all of them. mypy did not catch it (the call was `**`-free but the parent is
untyped at that boundary), and no test loaded a built-in profile through the
subclass.

This compares the override against the library method it inherits, so the next
time metaseed changes that signature the hub fails here rather than in
production.
"""

from __future__ import annotations

import inspect

from metaseed.specs.loader import SpecLoader

from metaseed_hub.ui.explore_routes import HubSpecLoader


def test_load_profile_matches_the_library_signature() -> None:
    parent = inspect.signature(SpecLoader.load_profile)
    override = inspect.signature(HubSpecLoader.load_profile)

    assert list(override.parameters) == list(parent.parameters), (
        f"override {override} does not match library {parent}"
    )


def test_a_built_in_profile_loads_through_the_subclass() -> None:
    spec = HubSpecLoader({}).load_profile("1.2", "miappe")

    assert spec.name == "miappe"
