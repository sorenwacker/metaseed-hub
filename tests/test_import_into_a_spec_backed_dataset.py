"""Importing a file works for datasets built on a hub specification.

`dataset_import_into_existing` needed the profile's root entity — the default
type for a payload with no `_type` marker — and obtained it by calling
`SpecLoader(profile=dataset.profile).load_profile(...)`. That loader only
knows built-in profiles. A dataset created from a SpecDraft or a published
Spec carries the draft's lowercased name as its profile, which no built-in
loader can resolve, so the call raised `SpecLoadError` outside any try block
and the route answered 500.

`AppState.get_root_entity_types` already answers this correctly for both
kinds of dataset — it reads the facade's injected spec when there is one and
only falls back to disk otherwise — so the route asks the state it already
loads instead of re-deriving the profile from scratch.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, SpecDraft
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade_for_write
from metaseed_hub.ui.routes.dataset.editor import dataset_import_into_existing
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))

_SPEC_DATA = {
    "name": "Fieldbook",
    "version": "1.0",
    "root_entity": "Trial",
    "entities": {
        "Trial": {
            "fields": [
                {"name": "trial_id", "type": "string", "is_identifier": True},
                {"name": "title", "type": "string"},
            ]
        }
    },
}


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


async def _draft_backed_dataset(session: AsyncSession) -> tuple[Dataset, TokenUser]:
    """A dataset bound to a hub draft, as the spec builder creates them."""
    sub = f"drafter-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.flush()

    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name="Fieldbook",
        version="1.0",
        spec_data=_SPEC_DATA,
    )
    session.add(draft)
    await session.flush()

    dataset = make_dataset(tenant=tenant, profile="fieldbook", version="1.0")
    dataset.spec_draft_id = draft.id
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email="d@example.org", name="D", roles=[])


async def test_the_loaded_state_knows_the_specs_root_entity(session: AsyncSession) -> None:
    """The state resolves database-stored specs, so it can name the root."""
    dataset, _ = await _draft_backed_dataset(session)

    state = await ensure_dataset_facade_for_write(dataset, session)

    assert state.get_root_entity_types() == ["Trial"]


async def test_an_untyped_payload_imports_against_a_draft_spec(session: AsyncSession) -> None:
    """The case that used to 500: no built-in profile named 'fieldbook' exists."""
    dataset, caller = await _draft_backed_dataset(session)
    payload = json.dumps({"trial_id": "T-1", "title": "First"}).encode()

    response = await dataset_import_into_existing(
        request=_csrf_request(),
        dataset_id=dataset.id,
        session=session,
        user=caller,
        file=UploadFile(file=BytesIO(payload), filename="trial.json"),
    )

    assert response.status_code == 200
    assert "error" not in response.body.decode().lower()

    state = await ensure_dataset_facade_for_write(dataset, session)
    assert [node.entity_type for node in state.nodes_by_id.values()] == ["Trial"]
