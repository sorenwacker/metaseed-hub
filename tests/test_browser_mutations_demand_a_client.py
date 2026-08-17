"""A browser mutation refuses a dataset it has no client for.

`ensure_dataset_facade_for_write` states the writer invariant: a route that
saves what it loaded cannot operate facade-less, and that demand extends to
writers of *empty* datasets — an empty dataset whose specification is missing
or broken will happily accept a first edit and then save it against whatever
facade `get_or_create_facade` improvises, which is not the specification the
dataset is bound to.

`get_dataset_state_for_mutation` had reimplemented the surrounding
skipped-node refusal rather than calling that helper, and the copy omitted
`require_client=True`. Every browser table, cell, and row edit funnels through
this dependency, so the invariant held everywhere except the main browser
mutation path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, SpecDraft
from metaseed_hub.ui.dependencies import get_dataset_state_for_mutation, tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.services.exceptions import DatasetDataLoadError
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


async def _empty_dataset_on_a_broken_draft(
    session: AsyncSession,
) -> tuple[Dataset, TokenUser]:
    """An empty dataset bound to a draft that holds no spec data."""
    sub = f"editor-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.flush()

    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name="Broken",
        version="1.0",
        spec_data=None,
    )
    session.add(draft)
    await session.flush()

    dataset = make_dataset(tenant=tenant, profile="broken", version="1.0")
    dataset.spec_draft_id = draft.id
    dataset.data = {}
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email="e@example.org", name="E", roles=[])


def _request(user: TokenUser) -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    request.state.user = user
    return request


async def test_an_empty_dataset_without_a_client_refuses_the_mutation(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, user = await _empty_dataset_on_a_broken_draft(session)
    import metaseed_hub.ui.dependencies as deps

    monkeypatch.setattr(deps, "get_current_user_from_cookie", AsyncMock(return_value=user))

    with pytest.raises(DatasetDataLoadError):
        await get_dataset_state_for_mutation(
            request=_request(user),
            dataset_id=dataset.id,
            session=session,
        )
