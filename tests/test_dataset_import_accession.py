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


async def test_import_by_accession_records_version_author(session):
    """The accession import was the one save path without an author."""
    from sqlalchemy import select

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.models import DatasetVersion

    tenant = await _add(session, make_tenant())
    db_user = await _add(session, make_user(tenant=tenant))
    token = TokenUser(sub=db_user.keycloak_id, email=db_user.email, name="n", roles=[])

    with patch("metaseed.metabolights.import_accession", _fake_importer):
        dataset = await create_dataset_from_accession(
            session, tenant.id, "authored", "metabolights", "MTBLS0001", token
        )

    versions = (
        (
            await session.execute(
                select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
            )
        )
        .scalars()
        .all()
    )
    assert versions, "the import should create a version"
    assert versions[0].created_by_id == db_user.id


def _empty_importer(_accession, **_kw):
    """An importer that ran fine but resolved the accession to nothing."""
    return MetaseedClient("metabolights", "1.0")


async def test_import_that_found_nothing_raises_value_error(session):
    """Distinct from LookupError so the caller does not blame a missing importer."""
    tenant = await _add(session, make_tenant())
    with (
        patch("metaseed.metabolights.import_accession", _empty_importer),
        pytest.raises(ValueError),
    ):
        await create_dataset_from_accession(session, tenant.id, "x", "metabolights", "MTBLS404")


async def test_route_reports_empty_result_as_import_empty(session):
    """The redirect previously said no_importer, the wrong diagnosis."""
    from unittest.mock import Mock

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
    from metaseed_hub.ui.routes.dataset.crud import dataset_import_accession

    csrf = get_or_create_csrf_token(Mock(cookies={}))
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: csrf}
    request.headers = {"X-CSRF-Token": csrf}
    token = TokenUser(sub="empty-import-caller", email="e@example.org", name="E", roles=[])

    with patch("metaseed.metabolights.import_accession", _empty_importer):
        response = await dataset_import_accession(
            request,
            session,
            token,
            profile="metabolights",
            accession="MTBLS404",
            name="nothing",
            csrf_token=csrf,
        )

    assert response.status_code == 302
    assert "error=import_empty" in response.headers["location"]


async def test_save_dataset_state_records_version_author(session):
    """Passing the acting user records them as the dataset version's author.

    Previously save_dataset_state's user_id param was never supplied, so
    created_by_id was always None.
    """
    from sqlalchemy import select

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.models import DatasetVersion
    from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state

    tenant = await _add(session, make_tenant())
    db_user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))

    state = await ensure_dataset_facade(dataset, session)
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


async def test_the_importer_runs_off_the_event_loop(session):
    """Same fix as the in-dataset import: the archive fetch is blocking HTTP and
    must not stall the loop that serves every other request."""
    import threading

    tenant = await _add(session, make_tenant())
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def _records_thread(accession, **_kw):
        seen.append(threading.get_ident())
        return _fake_importer(accession)

    with patch("metaseed.metabolights.import_accession", _records_thread):
        await create_dataset_from_accession(
            session, tenant.id, "imported", "metabolights", "MTBLS0000"
        )

    assert seen and seen[0] != loop_thread, "the importer ran on the event loop"
