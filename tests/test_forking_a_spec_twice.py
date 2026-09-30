"""Forking a published specification twice gives the second fork a suffixed name.

The fork route created the draft at the specification's own name and version,
and a draft is one name at one version per user, so the second fork raised
IntegrityError on ``uq_spec_drafts_tenant_user_name_version``: a 500 for the
ordinary act of forking again. A second draft from a template already takes a
suffixed name through ``free_draft_name``; a fork now does the same.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio


def _fork() -> Any:
    router = APIRouter()
    register_draft_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    for route in router.routes:
        if route.path.endswith("/spec/{spec_id}/edit") and "POST" in route.methods:  # type: ignore[attr-defined]
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError("no fork route")


async def test_the_second_fork_is_a_draft_with_a_suffixed_name(session: AsyncSession) -> None:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant, email="author@example.org")
    forker = make_user(tenant=tenant, email="forker@example.org")
    session.add_all([author, forker])
    await session.flush()
    spec = make_spec(
        tenant=tenant,
        created_by=author,
        name="forkable",
        version="1.0",
        spec_data=SpecBuilderState(spec=ProfileSpec(name="forkable", version="1.0")).to_dict(),
    )
    session.add(spec)
    await session.commit()
    fork = _fork()
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    first = await fork(
        request=request, spec_id=spec.id, session=session, user_ctx=(forker.id, tenant.id)
    )
    second = await fork(
        request=request, spec_id=spec.id, session=session, user_ctx=(forker.id, tenant.id)
    )

    assert (first.status_code, second.status_code) == (302, 302)
    names = sorted(
        (
            await session.execute(select(SpecDraft.name).where(SpecDraft.user_id == forker.id))
        ).scalars()
    )
    assert len(names) == 2 and names[0] == "forkable" and names[1].startswith("forkable-"), names
