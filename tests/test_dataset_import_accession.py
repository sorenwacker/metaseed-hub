"""Tests for accession-based dataset import (reusing metaseed's importer registry)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from metaseed import MetaseedClient

from metaseed_hub.models import Dataset
from metaseed_hub.ui.routes.dataset.crud import create_dataset_from_accession
from tests.factories import make_tenant

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
