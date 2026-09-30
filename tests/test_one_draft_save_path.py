"""``save_state_to_draft`` is the only writer of ``SpecDraft.spec_data``.

Three paths assigned the column themselves: the MCP ``_building`` context, the
REST push's update of an existing draft, and the reset route. None took the row
lock, compared the revision the state was read at, or routed the name through
``free_draft_name``. An agent's edit that began before a browser save and
committed after it discarded that save while both reported success, and a
version change onto a version the account already held raised IntegrityError.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from metaseed.specs.schema import EntityDefSpec, ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.mcp import _building
from metaseed_hub.models import SpecDraft, User
from metaseed_hub.ui.spec_builder.access import (
    DraftConflictError,
    load_state_for_draft,
    save_state_to_draft,
)
from metaseed_hub.ui.spec_builder.cache import state_cache
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec_draft, make_tenant, make_user
from tests.mcp_helpers import _calling_with, _drafting, _tool

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
ACCESS_MODULE = SRC / "ui" / "spec_builder" / "access.py"


def _spec(*entity_names: str) -> ProfileSpec:
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity=entity_names[0],
        entities={
            name: EntityDefSpec(description=f"{name} entity", fields=[]) for name in entity_names
        },
    )


async def _draft(session: AsyncSession) -> tuple[SpecDraft, User]:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant,
        user=user,
        name="demo",
        spec_data=SpecBuilderState(spec=_spec("Investigation")).to_dict(),
    )
    session.add(draft)
    await session.commit()
    state_cache.pop(draft.id)
    return draft, user


async def test_an_agent_edit_does_not_discard_a_browser_save_made_meanwhile(
    session: AsyncSession,
) -> None:
    """Before: the agent's copy overwrote the row and both reported success."""
    draft, user = await _draft(session)

    with pytest.raises(DraftConflictError):
        async with _building(session, draft, user) as builder:
            # A browser save lands while the agent holds its copy.
            browser_state, browser_draft = await load_state_for_draft(session, draft.id, user.id)
            assert browser_state.spec is not None
            browser_state.spec.entities["Sample"] = EntityDefSpec(description="added in browser")
            await save_state_to_draft(session, browser_state, browser_draft)
            builder.set_metadata(display_name="Agent")

    await session.refresh(draft)
    assert "Sample" in draft.spec_data["spec"]["entities"], "the browser's edit survived"


async def test_an_agent_version_change_onto_an_occupied_version_is_saved_like_a_browser_save(
    server, session: AsyncSession
) -> None:
    """Before: IntegrityError on ``uq_spec_drafts_tenant_user_name_version``."""
    secret = await _drafting(server, session, slug="dsp00001", name="coll")
    create = await _tool(server, "spec_create")
    set_metadata = await _tool(server, "spec_set_metadata")
    with _calling_with(secret):
        await create("coll", "2.0", "the next version")
        await set_metadata("coll@1.0", version="2.0")

    rows = (
        await session.execute(select(SpecDraft.name, SpecDraft.version).order_by(SpecDraft.name))
    ).all()
    assert [v for _n, v in rows] == ["2.0", "2.0"]
    assert len({n for n, _v in rows}) == 2, (
        "the moved draft took a free name, as a browser save does"
    )


# --- gate -------------------------------------------------------------------


def _offending_writes() -> list[str]:
    found = []
    for path in SRC.rglob("*.py"):
        if path == ACCESS_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "spec_data"
                    and isinstance(target.value, ast.Name)
                    and "draft" in target.value.id.lower()
                ):
                    found.append(
                        f"{path.relative_to(SRC)}:{node.lineno} {target.value.id}.spec_data ="
                    )
    return found


def test_the_gate_sees_the_save_path() -> None:
    assert "draft.spec_data = spec_data" in ACCESS_MODULE.read_text(encoding="utf-8")


def test_draft_spec_data_is_written_only_by_the_save_path() -> None:
    offenders = _offending_writes()
    assert not offenders, "load a state and call save_state_to_draft:\n" + "\n".join(offenders)
