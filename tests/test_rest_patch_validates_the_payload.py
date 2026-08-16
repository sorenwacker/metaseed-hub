"""The REST surface must not skip the load guard the UI enforces (260817).

`PATCH /api/datasets/{id}` assigned `dataset.data` and committed. Every UI
mutation goes through `get_dataset_state_for_mutation`, which refuses a payload
whose nodes the profile cannot place — because saving serializes the loaded
facade, so anything that did not load is deleted by the next save. The REST
route wrote the payload straight in, so an API client could store a dataset
that the UI then silently truncates.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.factories import make_dataset, make_tenant, make_user


@pytest.mark.asyncio
async def test_a_payload_that_cannot_load_is_refused(session: AsyncSession) -> None:
    from fastapi import HTTPException

    from metaseed_hub.api.datasets import _validated_data

    tenant = make_tenant(slug=tenant_slug_for("patch-kc"))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id="patch-kc"))
    dataset = make_dataset(tenant=tenant, name="patch-probe", profile="miappe", version="1.2")
    session.add(dataset)
    await session.commit()

    unloadable = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [{"id": "x", "entity_type": "NotAnEntity", "data": {}, "children": []}],
    }

    with pytest.raises(HTTPException) as raised:
        await _validated_data(dataset, unloadable, session)

    assert raised.value.status_code in (409, 422)


@pytest.mark.asyncio
async def test_a_loadable_payload_is_accepted(session: AsyncSession) -> None:
    from metaseed_hub.api.datasets import _validated_data

    tenant = make_tenant(slug=tenant_slug_for("patch-ok-kc"))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id="patch-ok-kc"))
    dataset = make_dataset(tenant=tenant, name="patch-ok", profile="miappe", version="1.2")
    session.add(dataset)
    await session.commit()

    good = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [],
            }
        ],
    }

    assert await _validated_data(dataset, good, session) == good
