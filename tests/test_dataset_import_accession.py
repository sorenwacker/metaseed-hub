"""Tests for accession-based dataset import (reusing metaseed's importer registry)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from metaseed import MetaseedClient

from metaseed_hub.models import Dataset
from metaseed_hub.ui.routes.dataset.crud import create_dataset_from_accession
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _add(session, obj):
    session.add(obj)
    await session.flush()
    return obj


def _fake_importer(accession, **_kw):
    client = MetaseedClient("metabolights", "1.0")
    client.create_entity(
        "Investigation",
        {"identifier": accession, "title": "Imported study", "description": "d"},
        skip_validation=True,
    )
    return client


async def test_import_by_accession_creates_dataset_with_entities(session):
    tenant = await _add(session, make_tenant())

    # Patch the MetaboLights importer that the registry action resolves to, so no
    # network call is made but the registry path is exercised exactly as in prod.
    with patch("metaseed.metabolights.import_accession", _fake_importer):
        dataset = await create_dataset_from_accession(
            session, tenant.id, "imported", "metabolights", "MTBLS0000"
        )

    assert isinstance(dataset, Dataset)
    assert dataset.profile == "metabolights"
    # The imported Investigation is persisted into the dataset's tree.
    import json

    blob = json.dumps(dataset.data)
    assert dataset.data
    assert "MTBLS0000" in blob
    assert "Investigation" in blob


async def test_import_by_accession_unknown_profile_raises(session):
    tenant = await _add(session, make_tenant())
    with pytest.raises(LookupError):
        await create_dataset_from_accession(session, tenant.id, "x", "darwin-core", "X")


async def test_save_dataset_state_records_version_author(session):
    """Passing the acting user records them as the dataset version's author.

    Previously save_dataset_state's user_id param was never supplied, so
    created_by_id was always None.
    """
    from sqlalchemy import select

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.models import DatasetVersion
    from metaseed_hub.ui.helpers.dataset_state import get_dataset_state, save_dataset_state

    tenant = await _add(session, make_tenant())
    db_user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))

    state = get_dataset_state(dataset)
    token = TokenUser(sub=db_user.keycloak_id, email=db_user.email, name="n", roles=[])
    await save_dataset_state(session, dataset, state, token)

    versions = (
        (
            await session.execute(
                select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
            )
        )
        .scalars()
        .all()
    )
    assert versions, "a version should be created"
    assert versions[0].created_by_id == db_user.id
