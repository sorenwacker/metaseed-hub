"""A dataset that will not open says why.

The refusal read "No client for dataset <uuid>; cannot load its stored
entities" -- true, but it names nothing the owner can act on. In production the
actual cause was a profile version that no longer exists
(``SpecLoadError: Version not found``), which the ``except Exception`` above the
refusal had already swallowed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.services.exceptions import DatasetDataLoadError
from tests.factories import make_dataset, make_tenant

pytestmark = pytest.mark.asyncio


async def test_a_missing_profile_version_is_named_in_the_refusal(
    session: AsyncSession,
) -> None:
    tenant = make_tenant(slug=f"t{uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    dataset = make_dataset(tenant=tenant, profile="miappe", version="99.0")
    # Stored entities are what force the refusal; without them an empty state
    # is legitimate and nothing is raised.
    dataset.data = {"entities": [{"id": "n1", "entity_type": "Investigation", "data": {}}]}
    session.add(dataset)
    await session.commit()

    with pytest.raises(DatasetDataLoadError) as exc_info:
        await ensure_dataset_facade(dataset, session)

    message = str(exc_info.value)
    assert "miappe" in message and "99.0" in message, message
    assert "could not be loaded" in message, message
    assert "99.0" in exc_info.value.user_message, exc_info.value.user_message
