"""A draft or specification row becomes a ``SpecBuilderState`` in one place.

The sequence ``SpecBuilderState.from_dict(row.spec_data) if row.spec_data
else SpecBuilderState()`` followed by an ``is None`` check on the spec was
written eight times across the spec-builder routes, the REST spec routes and
the MCP tools; three copies is how one of them stops handling an empty row.
``state_of`` in ``ui/spec_builder/access.py`` is the one loader now.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from metaseed.specs.schema import ProfileSpec

from metaseed_hub.ui.spec_builder.access import state_of
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec_draft, make_tenant, make_user

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
LOADER = SRC / "ui" / "spec_builder" / "access.py"


def test_an_empty_row_loads_as_an_empty_state() -> None:
    draft = make_spec_draft(tenant=make_tenant(), user=make_user(tenant=make_tenant()))
    draft.spec_data = None
    assert state_of(draft).spec is None


def test_a_stored_row_loads_its_spec() -> None:
    tenant = make_tenant()
    draft = make_spec_draft(
        tenant=tenant,
        user=make_user(tenant=tenant),
        spec_data=SpecBuilderState(spec=ProfileSpec(name="demo", version="1.0")).to_dict(),
    )
    assert state_of(draft).spec is not None
    assert state_of(draft).spec.name == "demo"


def test_only_the_loader_builds_a_state_from_a_row() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*.py")
        if path != LOADER
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "SpecBuilderState.from_dict(" in line
    ]
    assert not offenders, "load through state_of:\n" + "\n".join(offenders)


@pytest.mark.parametrize("marker", ["def state_of(", "SpecBuilderState.from_dict("])
def test_the_gate_sees_the_loader(marker: str) -> None:
    assert marker in LOADER.read_text(encoding="utf-8")
