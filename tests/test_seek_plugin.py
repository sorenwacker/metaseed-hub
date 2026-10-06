"""The hub SEEK plugin, available to every signed-in user.

SEEK is an adapter like ENA and DCAT; the per-group FeatureGrant gate that
hid it (from everyone — nothing ever wrote a grant row) is gone. The real
gate is the connection itself: without a configured SEEK API key there is
nothing to push with. The connection is edited on the profile page, beside
the other per-user credentials; ``/hub/seek/settings`` redirects there. Requests that write are
driven through ``ASGITransport`` rather than ``TestClient``, whose own event
loop cannot share the fixture's connection pool.

The HTTP-level tests talk to the app over ``https://test``: the CSRF cookie is
``Secure`` whenever DEBUG is off (CI), and the client sends it back only over a
secure origin. Over ``http`` every POST here would be a 403 once the SEEK
routes validated the token, which they now do.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.main import create_app
from metaseed_hub.models import SeekConnection
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_spec, make_spec_draft, make_tenant, make_user

PROFILE = "/hub/auth/profile"
SEEK_PAGE = "/hub/seek"


def _user() -> TokenUser:
    return TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])


@pytest.fixture
async def app_db(session):
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


@pytest.fixture
async def dataset(session: AsyncSession):
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    ds = make_dataset(tenant=tenant, profile="seek-ready-template", version="3.0")
    session.add(ds)
    await session.commit()
    return ds


HUB_SPEC = "hub-stored-isa"
"""A specification name no installed profile has: it exists only in the database."""


def _hub_spec_data() -> dict:
    """The SEEK-ready template under another name, as the spec builder stores it."""
    from metaseed.specs.loader import SpecLoader

    from metaseed_hub.ui.spec_builder.state import spec_to_dict

    spec = SpecLoader().load_profile("3.0", "seek-ready-template")
    return {"spec": {**spec_to_dict(spec), "name": HUB_SPEC}}


async def _hub_spec_dataset(session: AsyncSession, *, published: bool):
    """A dataset bound to a specification stored in the hub, not on disk."""
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    await session.flush()
    ds = make_dataset(tenant=tenant, profile=HUB_SPEC, version="3.0")
    if published:
        spec = make_spec(
            tenant=tenant, created_by=user, name=HUB_SPEC, version="3.0", spec_data=_hub_spec_data()
        )
        session.add(spec)
        await session.flush()
        ds.spec_id = spec.id
    else:
        draft = make_spec_draft(
            tenant=tenant, user=user, name=HUB_SPEC, version="3.0", spec_data=_hub_spec_data()
        )
        session.add(draft)
        await session.flush()
        ds.spec_draft_id = draft.id
    session.add(ds)
    await session.commit()
    return ds


@pytest.fixture
async def draft_dataset(session: AsyncSession):
    return await _hub_spec_dataset(session, published=False)


@pytest.fixture
async def published_dataset(session: AsyncSession):
    return await _hub_spec_dataset(session, published=True)


def _signed_in():
    """Patch authentication for one request."""
    return patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_user()),
    )


async def _get(path: str) -> httpx.Response:
    app = create_app()
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.get(path)


async def _post(path: str, factory_setup=None, **patches) -> httpx.Response:
    """POST a SEEK dataset action with a CSRF token and a faked SEEK client."""
    from contextlib import ExitStack

    app = create_app()
    with ExitStack() as stack:
        stack.enter_context(_signed_in())
        factory = stack.enter_context(patch("metaseed.seek.client_from_settings"))
        if factory_setup:
            factory_setup(factory)
        for target, replacement in patches.items():
            stack.enter_context(patch(target.replace("__", "."), replacement))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            page = await client.get(SEEK_PAGE)
            csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
            return await client.post(path, data={"csrf_token": csrf}, cookies=page.cookies)


async def _save_settings(url: str, seek_behaviour):
    """POST the connection form, with SEEK's client faked by ``seek_behaviour``."""
    app = create_app()
    with _signed_in(), patch("metaseed.seek.client_from_settings") as factory:
        seek_behaviour(factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            page = await client.get(SEEK_PAGE)
            csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
            return await client.post(
                "/hub/seek/settings",
                data={"url": url, "api_key": "k", "csrf_token": csrf},
                cookies=page.cookies,
            )


def _working(factory) -> None:
    factory.return_value.list_projects.return_value = [("1", "Tulip")]


def _unreachable(factory) -> None:
    factory.return_value.list_projects.side_effect = httpx.ConnectError("no")


class TestCrypto:
    def test_a_secret_round_trips(self) -> None:
        assert decrypt_secret(encrypt_secret("s3cr3t")) == "s3cr3t"

    def test_garbage_is_none_not_an_exception(self) -> None:
        assert decrypt_secret("not-a-token") is None


class TestTheSeekPage:
    """SEEK had no page: /hub/seek redirected to a section of the profile, the
    actions sat in the sidebar of a dataset, and nothing in the header led to
    either. The page holds the steps in the order they have to happen."""

    async def test_the_header_leads_to_it(self, dataset, app_db) -> None:
        html = (await _get("/hub/people")).text
        assert 'href="/hub/seek"' in html
        assert 'data-testid="nav-seek"' in html

    async def test_it_holds_the_connection_form(self, dataset, app_db) -> None:
        response = await _get(SEEK_PAGE)
        assert response.status_code == 200
        assert 'data-testid="seek-step-connection"' in response.text
        assert 'data-testid="seek-api-key"' in response.text

    async def test_the_old_settings_url_leads_there(self, dataset, app_db) -> None:
        response = await _get("/hub/seek/settings")
        assert response.status_code == 302
        assert response.headers["location"] == SEEK_PAGE

    async def test_saving_the_connection_returns_to_it(self, dataset, app_db) -> None:
        response = await _save_settings("https://seek.example.org", _working)
        assert response.headers["location"] == SEEK_PAGE

    async def test_the_profile_shows_the_standing_and_links_there(self, dataset, app_db) -> None:
        html = (await _get(PROFILE)).text
        assert 'data-testid="seek-status-none"' in html
        assert 'data-testid="seek-api-key"' not in html, "one form, in one place"
        assert 'data-testid="profile-seek-link"' in html

    async def test_the_project_step_waits_for_a_connection(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert 'data-testid="seek-step-project"' in html
        assert 'data-testid="seek-project"' not in html
        assert "Connect first" in html

    async def test_it_lists_a_dataset_that_can_be_pushed(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert f'data-testid="seek-dataset-{dataset.id}"' in html
        assert f'hx-post="/hub/seek/datasets/{dataset.id}/push"' in html
        assert f'hx-post="/hub/seek/datasets/{dataset.id}/check"' in html
        assert f'href="/hub/seek/datasets/{dataset.id}/templates"' in html

    async def test_it_lists_a_dataset_on_a_hub_stored_specification(
        self, draft_dataset, app_db
    ) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert f'data-testid="seek-dataset-{draft_dataset.id}"' in html

    async def test_a_dataset_that_cannot_be_pushed_is_counted_not_listed(
        self, dataset, session, app_db
    ) -> None:
        from metaseed_hub.models import Tenant

        tenant = (await session.execute(select(Tenant))).scalar_one()
        ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
        session.add(ena)
        await session.commit()

        html = (await _get(SEEK_PAGE)).text
        assert f'data-testid="seek-dataset-{ena.id}"' not in html
        assert 'data-testid="seek-unpushable"' in html
        assert "1 of your datasets cannot be pushed" in html

    async def test_check_and_push_wait_for_a_working_connection(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        row = html.split(f'data-testid="seek-dataset-{dataset.id}"')[1].split("</li>")[0]
        push = row.split('data-testid="btn-seek-push"')[0].rsplit("<button", 1)[1]
        check = row.split('data-testid="btn-seek-check"')[0].rsplit("<button", 1)[1]
        assert "disabled" in push and "disabled" in check
        assert "Connect your SEEK account first" in row
        templates = row.split('data-testid="btn-seek-templates"')[0].rsplit("<a", 1)[1]
        assert "disabled" not in templates, "the templates need no connection"

    async def test_a_working_connection_enables_them(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        row = html.split(f'data-testid="seek-dataset-{dataset.id}"')[1].split("</li>")[0]
        push = row.split('data-testid="btn-seek-push"')[0].rsplit("<button", 1)[1]
        assert "disabled" not in push

    async def test_the_dataset_sidebar_links_to_it(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="btn-seek-page"' in html
        assert "/hub/auth/profile#seek" not in html


class TestThePanelIsForEverySignedInUser:
    async def test_the_panel_shows_on_a_seek_ready_dataset(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="seek-panel"' in html
        assert 'data-testid="btn-seek-push"' in html


class TestSaving:
    async def test_a_working_connection_is_stored_and_verified(
        self, dataset, app_db, session
    ) -> None:
        response = await _save_settings("https://seek.example.org", _working)
        assert response.status_code == 303

        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.url == "https://seek.example.org"
        assert decrypt_secret(stored.api_key_encrypted) == "k"
        assert stored.verified_at is not None
        assert stored.last_error is None

    async def test_a_failed_check_still_stores_what_was_typed(
        self, dataset, app_db, session
    ) -> None:
        """Losing the settings to a failed check meant retyping the API key to
        fix a typo in the URL, and losing a good key to a SEEK briefly down."""
        await _save_settings("https://seek.example.org", _unreachable)

        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.url == "https://seek.example.org"
        assert decrypt_secret(stored.api_key_encrypted) == "k"
        assert stored.verified_at is None
        assert "Nothing answered" in (stored.last_error or "")

    async def test_a_projectless_seek_is_a_working_connection(
        self, dataset, app_db, session
    ) -> None:
        """Reaching SEEK with a valid key is a working connection; an account in
        no project is a thing to fix in SEEK, not a reason to refuse the key."""

        def projectless(factory) -> None:
            factory.return_value.list_projects.return_value = []

        await _save_settings("https://seek.example.org", projectless)

        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.verified_at is not None, "the key was accepted"
        assert "no project" in (stored.last_error or ""), (
            "but a push cannot land anywhere, and the status must say why"
        )

    async def test_the_key_is_never_rendered_back(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        stored = (await session.execute(select(SeekConnection))).scalar_one()
        html = (await _get(SEEK_PAGE)).text
        assert stored.api_key_encrypted not in html
        assert ">k<" not in html


class TestTheStatusIsShown:
    async def test_a_working_connection_reads_as_working(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        assert 'data-testid="seek-status-ok"' in html
        assert "https://seek.example.org" in html

    async def test_a_broken_connection_shows_its_reason(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _unreachable)
        html = (await _get(SEEK_PAGE)).text
        assert 'data-testid="seek-status-bad"' in html
        assert "Nothing answered" in html

    async def test_the_dataset_panel_shows_the_status_too(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="seek-status-ok"' in html

    async def test_nothing_configured_says_so(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert 'data-testid="seek-status-none"' in html


class TestVerificationSaysWhatFailed:
    """One message for four causes sent the owner changing the URL when the key
    and the URL were both fine."""

    def test_a_name_that_does_not_resolve_says_so(self) -> None:
        import socket

        from metaseed_hub.ui.routes.seek import _verification_failure

        message = _verification_failure(
            socket.gaierror("[Errno -3] Temporary failure in name resolution"),
            "https://seek.local:3000",
        )
        assert "cannot resolve seek.local:3000" in message

    def test_a_refused_connection_is_not_blamed_on_the_key(self) -> None:
        from metaseed_hub.ui.routes.seek import _verification_failure

        message = _verification_failure(httpx.ConnectError("refused"), "https://seek.example.org")
        assert "Nothing answered" in message
        assert "key" not in message.lower()

    def test_a_rejected_key_says_the_key(self) -> None:
        from metaseed_hub.ui.routes.seek import _verification_failure

        exc = httpx.HTTPStatusError(
            "401",
            request=httpx.Request("GET", "https://seek.example.org/projects"),
            response=httpx.Response(401),
        )
        message = _verification_failure(exc, "https://seek.example.org")
        assert "rejected the API key" in message

    def test_an_answer_that_is_not_seek_says_that(self) -> None:
        from metaseed_hub.ui.routes.seek import _verification_failure

        exc = httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "https://example.org/projects"),
            response=httpx.Response(404),
        )
        assert "not as a SEEK API" in _verification_failure(exc, "https://example.org")


class TestTheRouteBoundary:
    async def test_seek_routes_require_sign_in(self, dataset, app_db) -> None:
        """Anonymous requests are refused; the connection is the real gate."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/hub/seek/settings", follow_redirects=False)
        assert response.status_code in (302, 303, 401)


class TestTheStoredKeyIsKept:
    """The key is never rendered back, so the box is always empty. Requiring it
    on every save meant correcting a URL cost you the key."""

    async def test_a_blank_key_keeps_the_stored_one(self, dataset, app_db, session) -> None:
        from sqlalchemy import select

        from metaseed_hub.crypto import decrypt_secret
        from metaseed_hub.models import SeekConnection

        await _save_settings("https://seek.example.org", _working)

        app = create_app()
        with _signed_in(), patch("metaseed.seek.client_from_settings") as factory:
            _working(factory)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                await client.post(
                    "/hub/seek/settings",
                    data={
                        "url": "https://seek.example.org/moved",
                        "api_key": "",
                        "csrf_token": csrf,
                    },
                    cookies=page.cookies,
                )

        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.url == "https://seek.example.org/moved", "the URL changed"
        assert decrypt_secret(stored.api_key_encrypted) == "k", "the key survived"

    async def test_a_blank_key_with_nothing_stored_says_so(self, dataset, app_db, session) -> None:
        app = create_app()
        with _signed_in():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    "/hub/seek/settings",
                    data={
                        "url": "https://seek.example.org",
                        "api_key": "",
                        "csrf_token": csrf,
                    },
                    cookies=page.cookies,
                )

        assert response.status_code == 303
        assert "seek_error" in response.headers["location"]

    async def test_the_form_says_the_key_is_stored(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        assert "leave blank to keep it" in html.lower()

    async def test_a_stored_key_shows_stars_in_its_empty_field(
        self, dataset, app_db, session
    ) -> None:
        # The same sign metaseed's Plugins page gives: the key itself is never
        # sent back, so the stars are what says one is stored.
        stars = 'placeholder="********"'
        assert stars not in (await _get(SEEK_PAGE)).text

        await _save_settings("https://seek.example.org", _working)
        assert stars in (await _get(SEEK_PAGE)).text


class TestChoosingTheProject:
    """The push took the first project the instance returned, so anyone in more
    than one had no say in where their records landed."""

    @staticmethod
    def _two_projects(factory) -> None:
        factory.return_value.list_projects.return_value = [
            ("1", "Tulip"),
            ("7", "Resilience"),
        ]

    async def test_the_choices_are_offered(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", self._two_projects)
        html = (await _get(SEEK_PAGE)).text
        assert 'data-testid="seek-project"' in html
        assert "Resilience" in html and "Tulip" in html

    async def test_choosing_one_is_remembered(self, dataset, app_db, session) -> None:
        from sqlalchemy import select

        from metaseed_hub.models import SeekConnection

        await _save_settings("https://seek.example.org", self._two_projects)

        app = create_app()
        with _signed_in():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                await client.post(
                    "/hub/seek/project",
                    data={"project_id": "7", "csrf_token": csrf},
                    cookies=page.cookies,
                )

        session.expire_all()
        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.project_id == "7"
        assert stored.project_hint == "Resilience"

    async def test_a_later_check_keeps_the_choice(self, dataset, app_db, session) -> None:
        """Re-checking the connection must not silently move the target back to
        the first project."""
        from sqlalchemy import select

        from metaseed_hub.models import SeekConnection

        await _save_settings("https://seek.example.org", self._two_projects)
        stored = (await session.execute(select(SeekConnection))).scalar_one()
        stored.project_id = "7"
        stored.project_hint = "Resilience"
        await session.commit()

        await _save_settings("https://seek.example.org", self._two_projects)

        session.expire_all()
        stored = (await session.execute(select(SeekConnection))).scalar_one()
        assert stored.project_id == "7"


class TestTheIsaTemplates:
    """Sample Types and vocabularies are provisioned by the push; templates are
    not — only a SEEK administrator can install them, and SEEK's ISA-JSON
    export reads them. The file could not be obtained from the hub at all."""

    async def test_a_dataset_yields_its_templates(self, dataset, app_db) -> None:
        response = await _get(f"/hub/seek/datasets/{dataset.id}/templates")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "isa-templates.json" in response.headers["content-disposition"]
        assert response.json()["data"]

    async def test_an_unmappable_dataset_is_refused(self, session, app_db) -> None:
        from metaseed_hub.ui.dependencies import tenant_slug_for

        tenant = make_tenant(slug=tenant_slug_for("kc-1"))
        session.add(tenant)
        await session.flush()
        session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
        ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
        session.add(ena)
        await session.commit()

        response = await _get(f"/hub/seek/datasets/{ena.id}/templates")
        assert response.status_code == 422
        assert "does not map onto SEEK" in response.text

    async def test_the_panel_offers_the_download(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="btn-seek-templates"' in html
        assert f'href="/hub/seek/datasets/{dataset.id}/templates"' in html


class TestThePanelOnlyAppearsWhereItWorks:
    """The panel showed on every dataset, including ENA ones, where a push has
    nothing to hang records on: SEEK builds everything under an Investigation,
    and only a profile declaring that role has an ISA shape to map."""

    def test_the_seek_ready_template_qualifies(self) -> None:
        from metaseed.specs.loader import SpecLoader

        from metaseed_hub.ui.routes.seek import spec_supports_seek

        assert spec_supports_seek(SpecLoader().load_profile("3.0", "seek-ready-template"))

    @pytest.mark.parametrize(
        "profile,version",
        [("ena", "1.0"), ("pride", "1.0"), ("miappe", "1.2"), ("darwin-core", "1.0")],
    )
    def test_a_profile_without_isa_roles_does_not(self, profile: str, version: str) -> None:
        from metaseed.specs.loader import SpecLoader

        from metaseed_hub.ui.routes.seek import spec_supports_seek

        assert not spec_supports_seek(SpecLoader().load_profile(version, profile))

    def test_an_unresolved_specification_does_not(self) -> None:
        from metaseed_hub.ui.routes.seek import spec_supports_seek

        assert not spec_supports_seek(None)

    async def test_an_ena_dataset_shows_no_seek_panel(self, session, app_db) -> None:
        from metaseed_hub.ui.dependencies import tenant_slug_for

        tenant = make_tenant(slug=tenant_slug_for("kc-1"))
        session.add(tenant)
        await session.flush()
        session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
        ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
        session.add(ena)
        await session.commit()

        html = (await _get(f"/hub/datasets/{ena.id}")).text
        assert 'data-testid="seek-panel"' not in html
        assert 'data-testid="btn-seek-templates"' not in html

    async def test_pushing_one_anyway_says_why(self, session, app_db) -> None:
        """Someone with the URL, or a stale page, must get the reason rather
        than a failure from inside the sync."""
        from metaseed_hub.ui.dependencies import tenant_slug_for

        tenant = make_tenant(slug=tenant_slug_for("kc-1"))
        session.add(tenant)
        await session.flush()
        session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
        ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
        session.add(ena)
        await session.commit()

        app = create_app()
        with _signed_in():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    f"/hub/seek/datasets/{ena.id}/push",
                    data={"csrf_token": csrf},
                    cookies=page.cookies,
                )

        assert "does not describe an ISA structure" in response.text


class TestWhenSeekIsDown:
    """ "SEEK rejected GET /isa_tags (503): no available server" read as a bad
    request. The instance was down — every endpoint said the same — and the
    message sent the owner looking at the wrong thing."""

    def test_a_5xx_says_the_instance_is_down(self) -> None:
        from metaseed_hub.ui.routes.seek import _verification_failure

        exc = httpx.HTTPStatusError(
            "503",
            request=httpx.Request("GET", "https://seek.example.org/isa_tags"),
            response=httpx.Response(503, text="no available server"),
        )
        message = _verification_failure(exc, "https://seek.example.org")
        assert "not serving SEEK" in message
        assert "key" not in message.lower(), "the key is not the problem"

    def test_a_push_failure_reads_the_same_way(self) -> None:
        from metaseed_hub.ui.routes.seek import _push_failure

        exc = httpx.HTTPStatusError(
            "503",
            request=httpx.Request("GET", "https://seek.example.org/isa_tags"),
            response=httpx.Response(503),
        )
        assert "not serving SEEK" in _push_failure(exc, "https://seek.example.org")

    def test_a_rejected_key_still_names_the_key(self) -> None:
        from metaseed_hub.ui.routes.seek import _push_failure

        exc = httpx.HTTPStatusError(
            "401",
            request=httpx.Request("GET", "https://seek.example.org/projects"),
            response=httpx.Response(401),
        )
        assert "API key" in _push_failure(exc, "https://seek.example.org")

    def test_anything_else_is_reported_as_seek_said_it(self) -> None:
        from metaseed_hub.ui.routes.seek import _push_failure

        assert "sample type missing" in _push_failure(
            ValueError("sample type missing"), "https://seek.example.org"
        )


class TestTheReadinessCheck:
    """Three things fail separately and used to fail obscurely: ISA-JSON
    compliance switched off on the instance, templates not installed, and an
    unreachable SEEK."""

    async def test_it_names_the_missing_templates(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)

        app = create_app()
        with _signed_in(), patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.return_value = {}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    f"/hub/seek/datasets/{dataset.id}/check",
                    data={"csrf_token": csrf},
                    cookies=page.cookies,
                )

        assert "not installed" in response.text
        assert "templates/default_templates" in response.text
        assert "Compliance with ISA-JSON schemas" in response.text

    async def test_it_says_ready_when_they_are_there(self, dataset, app_db, session) -> None:
        from metaseed.seek.templates import template_title
        from metaseed.specs.loader import SpecLoader

        await _save_settings("https://seek.example.org", _working)
        profile = SpecLoader().load_profile("3.0", "seek-ready-template")
        installed = {
            template_title(profile, level): str(i)
            for i, level in enumerate(("study source", "study sample", "assay"), 1)
        }

        app = create_app()
        with _signed_in(), patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.return_value = installed
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    f"/hub/seek/datasets/{dataset.id}/check",
                    data={"csrf_token": csrf},
                    cookies=page.cookies,
                )

        assert "Ready" in response.text

    async def test_a_down_instance_says_so_rather_than_blaming_templates(
        self, dataset, app_db, session
    ) -> None:
        await _save_settings("https://seek.example.org", _working)

        app = create_app()
        with _signed_in(), patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.side_effect = httpx.HTTPStatusError(
                "503",
                request=httpx.Request("GET", "https://seek.example.org/templates"),
                response=httpx.Response(503),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test"
            ) as client:
                page = await client.get(SEEK_PAGE)
                csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
                response = await client.post(
                    f"/hub/seek/datasets/{dataset.id}/check",
                    data={"csrf_token": csrf},
                    cookies=page.cookies,
                )

        assert "not serving SEEK" in response.text

    async def test_the_panel_offers_it(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}")).text
        assert 'data-testid="btn-seek-check"' in html


class TestThePushSaysItIsWorking:
    """A push provisions and creates records over the network. With no sign it
    had started, the panel looked dead and the button invited a second press."""

    async def test_the_panel_announces_the_wait(self, dataset, app_db) -> None:
        """The notice is built by `htmx-handlers.js` from these attributes.

        It used to be an `hx-on::before-request` holding the markup inline,
        which htmx compiled with `new Function` -- rejected by the production
        Content-Security-Policy, so the notice never appeared there. The text
        lives in an attribute now and the element is created on send, carrying
        the same `seek-working` test id at runtime.
        """
        html = (await _get(f"/hub/datasets/{dataset.id}")).text

        assert f'data-working-target="seek-result-{dataset.id}"' in html, (
            "nothing tells the user a push is running"
        )
        assert "data-working-message=" in html, "the notice has no text"
        assert "hx-disabled-elt" in html, "the button can be pressed twice"

    def test_a_push_that_created_nothing_says_so(self) -> None:
        """Reporting 0 investigations, 0 studies as a success read as success."""
        from types import SimpleNamespace

        from metaseed_hub.ui.render import get_templates

        empty = SimpleNamespace(
            investigations=[],
            studies=[],
            assays=[],
            samples=[],
            errors=[],
            unlinked=[],
        )
        html = (
            get_templates()
            .get_template("partials/seek_panel_result.html")
            .render(result=empty, message=None, error=None)
        )
        assert 'data-testid="seek-result-nothing"' in html
        assert 'data-testid="seek-result-ok"' not in html


class TestADatasetOnAHubStoredSpecification:
    """The SEEK routes looked the profile up among the installed ones, by name.
    A dataset bound to a draft or a published specification has its profile in
    the database, so the lookup failed, read as "does not map onto SEEK", and
    hid the whole panel from exactly the specifications written for SEEK."""

    async def test_the_panel_appears_for_a_draft(self, draft_dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{draft_dataset.id}")).text
        assert 'data-testid="seek-panel"' in html

    async def test_the_panel_appears_for_a_published_specification(
        self, published_dataset, app_db
    ) -> None:
        html = (await _get(f"/hub/datasets/{published_dataset.id}")).text
        assert 'data-testid="seek-panel"' in html

    async def test_a_draft_yields_its_templates(self, draft_dataset, app_db) -> None:
        response = await _get(f"/hub/seek/datasets/{draft_dataset.id}/templates")
        assert response.status_code == 200
        names = [t["metadata"]["name"] for t in response.json()["data"]]
        assert names and all(HUB_SPEC in name for name in names)

    async def test_a_published_specification_yields_its_templates(
        self, published_dataset, app_db
    ) -> None:
        response = await _get(f"/hub/seek/datasets/{published_dataset.id}/templates")
        assert response.status_code == 200
        assert response.json()["data"]

    async def test_the_check_reads_its_templates(self, draft_dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)

        def nothing_installed(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = {}

        response = await _post(f"/hub/seek/datasets/{draft_dataset.id}/check", nothing_installed)
        assert "does not map onto SEEK" not in response.text
        assert "not installed" in response.text
        assert HUB_SPEC in response.text

    async def test_the_push_provisions_its_specification(self, draft_dataset, app_db) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        await _save_settings("https://seek.example.org", _working)
        plan = MagicMock()
        pushed = SimpleNamespace(
            investigations=["1"], studies=[], assays=[], samples=[], errors=[], unlinked=[]
        )

        response = await _post(
            f"/hub/seek/datasets/{draft_dataset.id}/push",
            metaseed__seek__build_provisioning_plan=plan,
            metaseed__seek__execute_provisioning_plan=MagicMock(),
            metaseed__seek__provision__resolve_cv_ids=MagicMock(return_value={}),
            metaseed__seek__sync_dataset_to_seek=MagicMock(return_value=pushed),
        )

        assert 'data-testid="seek-result-ok"' in response.text, response.text
        assert plan.call_args.args[0].name == HUB_SPEC


class TestAPushThatTakesLong:
    """A push to a small SEEK reported "timed out" with one Investigation
    created and nothing else. SEEK had answered ``POST /isa_assays`` after 35
    seconds; the client had given up at 30. The request succeeded and the hub
    called it an error, with no word that pushing again would continue."""

    async def test_the_push_waits_longer_than_seek_takes_to_build_an_assay(
        self, dataset, app_db
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        await _save_settings("https://seek.example.org", _working)
        pushed = SimpleNamespace(
            investigations=["1"], studies=[], assays=[], samples=[], errors=[], unlinked=[]
        )
        seen: dict = {}

        def remember(factory) -> None:
            seen["factory"] = factory

        await _post(
            f"/hub/seek/datasets/{dataset.id}/push",
            remember,
            metaseed__seek__build_provisioning_plan=MagicMock(),
            metaseed__seek__execute_provisioning_plan=MagicMock(),
            metaseed__seek__provision__resolve_cv_ids=MagicMock(return_value={}),
            metaseed__seek__sync_dataset_to_seek=MagicMock(return_value=pushed),
        )

        assert seen["factory"].call_args.kwargs["timeout"] >= 180

    def test_an_error_says_pushing_again_continues(self) -> None:
        from types import SimpleNamespace

        from metaseed_hub.ui.render import get_templates

        stopped = SimpleNamespace(
            investigations=["1"],
            studies=[],
            assays=[],
            samples=[],
            errors=[("node", "timed out")],
            unlinked=[],
        )
        html = (
            get_templates()
            .get_template("partials/seek_panel_result.html")
            .render(result=stopped, message=None, error=None)
        )
        assert 'data-testid="seek-result-resume"' in html
        assert "Push again" in html

    async def test_the_push_asks_to_confirm_the_project(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        row = html.split(f'data-testid="seek-dataset-{dataset.id}"')[1].split("</li>")[0]
        confirm = row.split('hx-confirm="')[1].split('"')[0]
        assert "Tulip" in confirm and "seek.example.org" in confirm
        assert dataset.name in confirm

    async def test_nothing_is_confirmed_without_a_connection(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert "hx-confirm" not in html.split(f'data-testid="seek-dataset-{dataset.id}"')[1]
