"""A member's role decides what they may do, not only whether they may look.

The sharing panel offers OWNER, EDITOR and VIEWER, and `sharing.EDIT_ROLES`
states which of them may change content — but until this file was red, nothing
on the content-mutation paths read it: `get_dataset_for_user` granted access to
any member regardless of role, and every mutation route authorized through it.
A viewer could edit and delete a dataset someone shared with them to look at.

The split under test:

- reads and comments: any member (VIEWER included) — `get_dataset_for_user`;
- content mutations: `EDIT_ROLES` only — `get_dataset_for_editor`, which
  `get_dataset_state_for_mutation` routes through, covering every browser
  table/cell/row edit at once;
- membership changes: owners only — `require_dataset_owner`, unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import metaseed_hub.ui.dependencies as deps_module
from metaseed_hub.auth import TokenUser
from metaseed_hub.models import DatasetMember, Role
from metaseed_hub.ui.dependencies import (
    get_dataset_for_editor,
    get_dataset_for_user,
    tenant_slug_for,
)
from tests.factories import make_dataset, make_tenant, make_user
from tests.test_tenant_isolation import _csrf_request


async def _shared_dataset(session: AsyncSession, role: Role, tag: str):
    """A dataset owned by one tenant, shared with a member at ``role``."""
    owner_tenant = make_tenant(slug=tenant_slug_for(f"own-{tag}-kc"))
    member_tenant = make_tenant(slug=tenant_slug_for(f"mem-{tag}-kc"))
    session.add_all([owner_tenant, member_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant, keycloak_id=f"own-{tag}-kc")
    member = make_user(tenant=member_tenant, keycloak_id=f"mem-{tag}-kc")
    session.add_all([owner, member])
    await session.flush()
    dataset = make_dataset(tenant=owner_tenant, name=f"shared-{tag}")
    session.add(dataset)
    await session.flush()
    session.add(DatasetMember(dataset_id=dataset.id, user_id=member.id, role=role))
    await session.commit()
    token = TokenUser(sub=f"mem-{tag}-kc", email=member.email, name="M", roles=[])
    return dataset, token


@pytest.mark.asyncio
async def test_a_viewer_may_read(session: AsyncSession) -> None:
    dataset, viewer = await _shared_dataset(session, Role.VIEWER, "read")

    got = await get_dataset_for_user(dataset.id, session, viewer)

    assert got.id == dataset.id


@pytest.mark.asyncio
async def test_a_viewer_may_not_edit(session: AsyncSession) -> None:
    """The finding itself: shared-to-look must not mean shared-to-change."""
    dataset, viewer = await _shared_dataset(session, Role.VIEWER, "edit")

    with pytest.raises(HTTPException) as caught:
        await get_dataset_for_editor(dataset.id, session, viewer)

    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_an_editor_may_edit(session: AsyncSession) -> None:
    dataset, editor = await _shared_dataset(session, Role.EDITOR, "ed")

    got = await get_dataset_for_editor(dataset.id, session, editor)

    assert got.id == dataset.id


@pytest.mark.asyncio
async def test_an_owner_member_may_edit(session: AsyncSession) -> None:
    dataset, owner = await _shared_dataset(session, Role.OWNER, "owm")

    got = await get_dataset_for_editor(dataset.id, session, owner)

    assert got.id == dataset.id


@pytest.mark.asyncio
async def test_every_browser_mutation_refuses_a_viewer(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_dataset_state_for_mutation` is the choke point for every table,
    cell and row edit, so enforcing the role there covers them all at once —
    the same argument its unloadable-node refusal already makes."""
    dataset, viewer = await _shared_dataset(session, Role.VIEWER, "mut")
    monkeypatch.setattr(deps_module, "get_current_user_from_cookie", AsyncMock(return_value=viewer))
    monkeypatch.setattr(deps_module, "validate_csrf_token", lambda request: True)

    with pytest.raises(HTTPException) as caught:
        await deps_module.get_dataset_state_for_mutation(
            request=_csrf_request(), dataset_id=dataset.id, session=session
        )

    assert caught.value.status_code == 403
    assert "view" in caught.value.detail.lower()


class TestTheRestApiSpeaksTheSameLadder:
    """The REST API answered tenant-owned datasets only, ignoring the sharing
    the UI honours — a dataset a colleague shared was editable in the browser
    and a 404 over the same account's token."""

    @pytest.mark.asyncio
    async def test_a_member_can_read_a_shared_dataset(self, session: AsyncSession) -> None:
        from metaseed_hub.api.datasets import get_dataset

        dataset, viewer = await _shared_dataset(session, Role.VIEWER, "api-r")

        got = await get_dataset(dataset.id, viewer, session)

        assert got.id == dataset.id

    @pytest.mark.asyncio
    async def test_a_viewer_patch_is_403_not_404(self, session: AsyncSession) -> None:
        """A member refused for their role already knows the dataset exists;
        the refusal explains itself instead of hiding."""
        from metaseed_hub.api.datasets import DatasetUpdate, update_dataset

        dataset, viewer = await _shared_dataset(session, Role.VIEWER, "api-p")

        with pytest.raises(HTTPException) as caught:
            await update_dataset(dataset.id, DatasetUpdate(name="renamed"), viewer, session)

        assert caught.value.status_code == 403

    @pytest.mark.asyncio
    async def test_a_stranger_still_sees_404(self, session: AsyncSession) -> None:
        """Across tenants the API must not disclose that the id exists."""
        from metaseed_hub.api.datasets import get_dataset
        from metaseed_hub.ui.dependencies import tenant_slug_for

        dataset, _viewer = await _shared_dataset(session, Role.VIEWER, "api-s")
        stranger_tenant = make_tenant(slug=tenant_slug_for("stranger-kc"))
        session.add(stranger_tenant)
        await session.flush()
        stranger = make_user(tenant=stranger_tenant, keycloak_id="stranger-kc")
        session.add(stranger)
        await session.commit()
        token = TokenUser(sub="stranger-kc", email=stranger.email, name="S", roles=[])

        with pytest.raises(HTTPException) as caught:
            await get_dataset(dataset.id, token, session)

        assert caught.value.status_code == 404

    @pytest.mark.asyncio
    async def test_an_editor_member_can_rename(self, session: AsyncSession) -> None:
        from metaseed_hub.api.datasets import DatasetUpdate, update_dataset

        dataset, editor = await _shared_dataset(session, Role.EDITOR, "api-e")

        got = await update_dataset(dataset.id, DatasetUpdate(name="renamed"), editor, session)

        assert got.name == "renamed"
