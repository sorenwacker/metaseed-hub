"""Filling an empty dataset from the source database its profile can import.

The hub holds no per-repository knowledge: which importer a profile offers, what
it is called, and what it asks for all come from metaseed's adapter registry.
These tests pin that, and pin the guard that keeps the importer from replacing
content a user authored.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from metaseed import MetaseedClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade,
    save_dataset_state,
)
from metaseed_hub.ui.routes.dataset.crud import dataset_import_source
from metaseed_hub.ui.routes.dataset.editor import _source_import_option
from tests.factories import make_dataset, make_tenant, make_user

# asyncio_mode is "auto", so the async tests here need no explicit mark.
_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


def _fake_pride_importer(accession: str, **_kw: object) -> MetaseedClient:
    client = MetaseedClient("pride", "1.0")
    client.create_entity(
        "Dataset",
        {"accession": accession, "title": "Imported project"},
        skip_validation=True,
    )
    return client


async def _dataset(session: AsyncSession, profile: str = "pride") -> tuple[Dataset, TokenUser]:
    sub = f"caller-import-{profile}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email="caller@example.org"))
    version = "1.0" if profile != "miappe" else "1.1"
    dataset = make_dataset(tenant=tenant, profile=profile, version=version)
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email="caller@example.org", name="C", roles=[])


def test_every_importable_profile_offers_its_own_control() -> None:
    """The label and prompt come from the registry, so a new adapter reaches the
    UI by being declared upstream rather than by editing the hub."""
    pride = _source_import_option("pride")
    assert pride is not None
    assert "accession" in pride["input_label"].lower()
    assert pride["placeholder"].startswith("PXD")

    # BrAPI imports into miappe and asks for a server URL, not an accession.
    miappe = _source_import_option("miappe")
    assert miappe is not None
    assert "URL" in miappe["input_label"]

    for profile in ("ena", "metabolights"):
        assert _source_import_option(profile) is not None, f"{profile} offers no importer"


def test_a_profile_without_an_importer_offers_nothing() -> None:
    assert _source_import_option("darwin-core") is None


async def test_importing_into_an_empty_dataset_fills_it(session: AsyncSession) -> None:
    dataset, token = await _dataset(session)

    with patch("metaseed.pride.import_accession", _fake_pride_importer):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="PXD000001"
        )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/hub/datasets/{dataset.id}"
    await session.refresh(dataset)
    blob = str(dataset.data)
    assert "PXD000001" in blob
    assert "Imported project" in blob


async def test_the_import_is_visible_to_the_editor(session: AsyncSession) -> None:
    """Persisting is not enough; the entity must load through the editor's path."""
    dataset, token = await _dataset(session)

    with patch("metaseed.pride.import_accession", _fake_pride_importer):
        await dataset_import_source(_csrf_request(), dataset.id, session, token, value="PXD000001")

    await session.refresh(dataset)
    state = await ensure_dataset_facade(dataset, session)
    assert [n.entity_type for n in state.nodes_by_id.values()] == ["Dataset"]


async def test_a_dataset_with_entities_is_refused(session: AsyncSession) -> None:
    """The importer replaces the whole tree, so running it over authored content
    would discard that content with no undo."""
    dataset, token = await _dataset(session)
    state = await ensure_dataset_facade(dataset, session)
    state.add_node(
        "Dataset",
        {"accession": "MINE", "title": "hand written"},
        skip_validation=True,
    )
    await save_dataset_state(session, dataset, state, token)

    with patch("metaseed.pride.import_accession", _fake_pride_importer):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="PXD000001"
        )

    assert response.status_code == 400
    await session.refresh(dataset)
    assert "hand written" in str(dataset.data), "the authored entity must survive"


async def test_a_profile_without_an_importer_is_refused(session: AsyncSession) -> None:
    dataset, token = await _dataset(session, profile="darwin-core")

    response = await dataset_import_source(
        _csrf_request(), dataset.id, session, token, value="anything"
    )

    assert response.status_code == 404


async def test_a_failing_import_reports_rather_than_raising(session: AsyncSession) -> None:
    """A bad accession or an archive outage must not 500 the dataset page."""
    dataset, token = await _dataset(session)

    def _boom(_accession: str, **_kw: object) -> MetaseedClient:
        raise RuntimeError("archive unavailable")

    with patch("metaseed.pride.import_accession", _boom):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="PXD000001"
        )

    assert response.status_code == 502
    await session.refresh(dataset)
    assert not dataset.data.get("tree"), "a failed import must leave the dataset empty"


async def test_import_without_csrf_is_rejected(session: AsyncSession) -> None:
    dataset, token = await _dataset(session)
    request = Mock()
    request.cookies = {}
    request.headers = {}

    response = await dataset_import_source(request, dataset.id, session, token, value="PXD000001")

    assert response.status_code == 403


def _empty_importer(_accession: str, **_kw: object) -> MetaseedClient:
    """An archive that resolves nothing — an accession that does not exist.

    The ENA importer returns an empty client rather than raising for an
    unresolvable accession, so this is what a typo actually produces.
    """
    return MetaseedClient("pride", "1.0")


async def test_an_import_that_found_nothing_says_so(session: AsyncSession) -> None:
    """Reported as a bug: a mistyped accession left an empty dataset and a
    success message, so the user had no idea the import had failed."""
    dataset, token = await _dataset(session)

    with patch("metaseed.pride.import_accession", _empty_importer):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="PXD99999999"
        )

    assert response.status_code == 404
    body = response.body.decode().lower()
    assert "nothing" in body or "no data" in body or "not found" in body


async def test_an_import_that_found_nothing_leaves_the_dataset_alone(
    session: AsyncSession,
) -> None:
    """It must not overwrite the dataset with the empty result it just got."""
    dataset, token = await _dataset(session)

    with patch("metaseed.pride.import_accession", _empty_importer):
        await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="PXD99999999"
        )

    await session.refresh(dataset)
    assert not dataset.data.get("tree"), "an empty import was saved over the dataset"


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _http_error(status: int) -> Exception:
    exc = RuntimeError(f"Client error '{status}' for url")
    exc.response = _Response(status)  # type: ignore[attr-defined]
    return exc


async def test_a_wrong_brapi_address_says_so_rather_than_blaming_the_identifier(
    session: AsyncSession,
) -> None:
    """Reported: no BrAPI endpoint would work. A server URL missing its
    ``/brapi/v2`` suffix 404s, and "check the identifier" sent the user hunting
    for a bad accession instead of a bad address."""
    dataset, token = await _dataset(session, profile="miappe")

    def _not_found(_url: str, **_kw: object) -> MetaseedClient:
        raise _http_error(404)

    with patch("metaseed.brapi.import_brapi", _not_found):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="https://server.example.org"
        )

    body = response.body.decode()
    assert "brapi/v2" in body, "the message must name the likely fix"
    assert "404" in body


async def test_an_address_that_is_not_an_api_says_so(session: AsyncSession) -> None:
    """A server returning HTML gives a JSON decode error, which as a raw string
    means nothing to a user."""
    from json import JSONDecodeError

    dataset, token = await _dataset(session, profile="miappe")

    def _html(_url: str, **_kw: object) -> MetaseedClient:
        raise JSONDecodeError("Expecting value", "<html>", 0)

    with patch("metaseed.brapi.import_brapi", _html):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="https://example.org/germinate"
        )

    body = response.body.decode()
    assert "not an API endpoint" in body or "not return JSON" in body


async def test_a_private_record_is_distinguished_from_a_missing_one(
    session: AsyncSession,
) -> None:
    dataset, token = await _dataset(session, profile="miappe")

    def _forbidden(_url: str, **_kw: object) -> MetaseedClient:
        raise _http_error(403)

    with patch("metaseed.brapi.import_brapi", _forbidden):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value="https://server.example.org/brapi/v2"
        )

    assert "refused access" in response.body.decode()


async def test_a_not_found_accession_is_reported_as_text_not_markup(
    session: AsyncSession,
) -> None:
    """The value is reflected input; the route escaped it in one branch only.

    `_import_failure_message` escapes carefully, and the exception branch uses
    it. The empty-result and missing-importer branches interpolated the raw
    form value and the stored profile name straight into the fragment htmx
    swaps into the page.
    """
    dataset, token = await _dataset(session)
    payload = "<script>alert(1)</script>"

    def _nothing(_accession: str, **_kw: object) -> MetaseedClient:
        return MetaseedClient("pride", "1.0")

    with patch("metaseed.pride.import_accession", _nothing):
        response = await dataset_import_source(
            _csrf_request(), dataset.id, session, token, value=payload
        )

    body = response.body.decode()
    assert response.status_code == 404
    assert payload not in body
    assert "&lt;script&gt;" in body


async def test_a_missing_importer_does_not_reflect_the_stored_profile(
    session: AsyncSession,
) -> None:
    """A draft-backed dataset's profile is the draft's name, which its author chose.

    The dataset must still load — the write-path loader refuses one whose spec
    it cannot resolve — so the profile carrying markup is a real draft name,
    not an arbitrary string.
    """
    from metaseed_hub.models import SpecDraft

    name = "<img src=x onerror=alert(1)>"
    dataset, token = await _dataset(session)
    draft = SpecDraft(
        tenant_id=dataset.tenant_id,
        user_id=(await _db_user(session, token)).id,
        name=name,
        version="1.0",
        spec_data={
            "name": name,
            "version": "1.0",
            "root_entity": "Trial",
            "entities": {
                "Trial": {"fields": [{"name": "trial_id", "type": "string", "is_identifier": True}]}
            },
        },
    )
    session.add(draft)
    await session.flush()
    dataset.spec_draft_id = draft.id
    dataset.profile = name
    dataset.version = "1.0"
    await session.commit()

    response = await dataset_import_source(
        _csrf_request(), dataset.id, session, token, value="anything"
    )

    body = response.body.decode()
    assert response.status_code == 404
    assert "<img src=x" not in body
    assert "&lt;img" in body


async def _db_user(session: AsyncSession, token) -> object:
    """The stored User row behind a token, for rows that need its id."""
    from sqlalchemy import select

    from metaseed_hub.models import User

    found = await session.execute(select(User).where(User.keycloak_id == token.keycloak_id))
    return found.scalar_one()
