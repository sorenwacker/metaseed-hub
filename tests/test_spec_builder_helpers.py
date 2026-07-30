"""Tests for spec_builder_helpers YAML output.

spec_to_yaml used to register its string representer on the process-global
``yaml.Dumper``, silently switching every later ``yaml.dump`` in the process
to block-style multi-line strings. The representer now lives on a dedicated
Dumper subclass, so the output keeps its block style while the global dumper
stays untouched.
"""

from __future__ import annotations

import yaml
from metaseed.specs.schema import EntityDefSpec, ProfileSpec

from metaseed_hub.ui.spec_builder_helpers import spec_to_yaml


def _spec_with_multiline_description() -> ProfileSpec:
    return ProfileSpec(
        name="demo",
        version="0.1",
        description="line one\nline two",
        root_entity="Investigation",
        entities={"Investigation": EntityDefSpec(description="root", fields=[])},
    )


def test_multiline_strings_render_in_block_style() -> None:
    output = spec_to_yaml(_spec_with_multiline_description())

    assert "description: |" in output
    assert "line one\n" in output


def test_the_global_yaml_dumper_is_not_mutated() -> None:
    spec_to_yaml(_spec_with_multiline_description())

    # A plain yaml.dump elsewhere in the process must keep PyYAML's default
    # quoting for multi-line strings rather than the spec dumper's block style.
    assert "|" not in yaml.dump({"x": "a\nb"})
