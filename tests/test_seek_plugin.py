"""The hub SEEK plugin, gated behind the seek feature.

The connection is edited on the profile page, beside the other per-user
credentials; ``/hub/seek/settings`` redirects there. Requests that write are
driven through ``ASGITransport`` rather than ``TestClient``, whose own event
loop cannot share the fixture's connection pool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.main import create_app
from metaseed_hub.models import SeekConnection
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_tenant, make_user

PROFILE = "/hub/auth/profile"


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


def _signed_in(features: set[str]):
    """Patch authentication and the feature set for one request."""
    return (
        patch(
            "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
            AsyncMock(return_value=_user()),
        ),
        patch(
            "metaseed_hub.ui.routes.dataset.editor.user_feature_set",
            AsyncMock(return_value=features),
        ),
        patch(
            "metaseed_hub.features.enabled_features",
            AsyncMock(return_value=features),
        ),
    )


async def _get(path: str, features: set[str]) -> httpx.Response:
    app = create_app()
    a, b, c = _signed_in(features)
    with a, b, c:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(path)


async def _save_settings(url: str, seek_behaviour, features: set[str] | None = None):
    """POST the connection form, with SEEK's client faked by ``seek_behaviour``."""
    app = create_app()
    a, b, c = _signed_in(features or {"seek"})
    with a, b, c, patch("metaseed.seek.client_from_settings") as factory:
        seek_behaviour(factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            page = await client.get(PROFILE)
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


class TestTheFormLivesOnTheProfilePage:
    async def test_the_seek_group_gets_the_section(self, dataset, app_db) -> None:
        html = (await _get(PROFILE, {"seek"})).text
        assert 'id="seek"' in html
        assert 'data-testid="seek-api-key"' in html

    async def test_without_the_grant_there_is_no_section(self, dataset, app_db) -> None:
        html = (await _get(PROFILE, set())).text
        assert 'data-testid="seek-api-key"' not in html

    async def test_the_old_settings_url_leads_there(self, dataset, app_db) -> None:
        response = await _get("/hub/seek/settings", {"seek"})
        assert response.status_code == 302
        assert response.headers["location"] == f"{PROFILE}#seek"

    async def test_the_bare_seek_url_leads_there_too(self, dataset, app_db) -> None:
        """/hub/seek was a 404 that read as 'the feature is gone'."""
        response = await _get("/hub/seek", {"seek"})
        assert response.status_code == 302

    async def test_settings_is_404_without_the_feature(self, dataset, app_db) -> None:
        assert (await _get("/hub/seek/settings", set())).status_code == 404


class TestThePanelIsGated:
    async def test_the_seek_group_sees_the_panel(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}", {"seek"})).text
        assert 'data-testid="seek-panel"' in html
        assert 'data-testid="btn-seek-push"' in html

    async def test_without_the_grant_the_panel_does_not_exist(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}", set())).text
        assert 'data-testid="seek-panel"' not in html


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
        html = (await _get(PROFILE, {"seek"})).text
        assert stored.api_key_encrypted not in html
        assert ">k<" not in html


class TestTheStatusIsShown:
    async def test_a_working_connection_reads_as_working(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(PROFILE, {"seek"})).text
        assert 'data-testid="seek-status-ok"' in html
        assert "https://seek.example.org" in html

    async def test_a_broken_connection_shows_its_reason(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _unreachable)
        html = (await _get(PROFILE, {"seek"})).text
        assert 'data-testid="seek-status-bad"' in html
        assert "Nothing answered" in html

    async def test_the_dataset_panel_shows_the_status_too(self, dataset, app_db, session) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(f"/hub/datasets/{dataset.id}", {"seek"})).text
        assert 'data-testid="seek-status-ok"' in html

    async def test_nothing_configured_says_so(self, dataset, app_db) -> None:
        html = (await _get(PROFILE, {"seek"})).text
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
    async def test_pushing_without_the_feature_is_404(self, dataset, app_db) -> None:
        app = create_app()
        a, b, c = _signed_in(set())
        with a, b, c:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/hub/seek/datasets/{dataset.id}/push", data={})
        assert response.status_code == 404

    def test_every_seek_route_requires_the_feature(self) -> None:
        """A route that forgot the dependency would be reachable by anyone."""
        from metaseed_hub.ui.routes import seek

        for route in seek.router.routes:
            dependencies = [
                d.call.__qualname__ if hasattr(d.call, "__qualname__") else str(d.call)
                for d in getattr(route, "dependant", None).dependencies
            ]
            assert any("feature" in name for name in dependencies), (
                f"{route.path} does not require the seek feature"
            )

    def test_the_client_refuses_an_unreadable_key(self) -> None:
        """A changed SECRET_KEY must say so, not fail deep inside a sync."""
        from metaseed_hub.ui.routes.seek import _client_for

        connection = SeekConnection(
            tenant_id="t", url="https://seek.example.org", api_key_encrypted="rubbish"
        )
        with pytest.raises(ValueError, match="cannot be read"):
            _client_for(connection)


def test_the_client_module_imports_without_the_seek_extra() -> None:
    """The seek extra is a hard dependency of these routes; a missing one takes
    the whole hub down at import, not just this feature."""
    assert TestClient is not None


class TestTheStoredKeyIsKept:
    """The key is never rendered back, so the box is always empty. Requiring it
    on every save meant correcting a URL cost you the key."""

    async def test_a_blank_key_keeps_the_stored_one(self, dataset, app_db, session) -> None:
        from sqlalchemy import select

        from metaseed_hub.crypto import decrypt_secret
        from metaseed_hub.models import SeekConnection

        await _save_settings("https://seek.example.org", _working)

        app = create_app()
        a, b, c = _signed_in({"seek"})
        with a, b, c, patch("metaseed.seek.client_from_settings") as factory:
            _working(factory)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get(PROFILE)
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
        a, b, c = _signed_in({"seek"})
        with a, b, c:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get(PROFILE)
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
        html = (await _get(PROFILE, {"seek"})).text
        assert "leave blank to keep it" in html.lower()


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
        html = (await _get(PROFILE, {"seek"})).text
        assert 'data-testid="seek-project"' in html
        assert "Resilience" in html and "Tulip" in html

    async def test_choosing_one_is_remembered(self, dataset, app_db, session) -> None:
        from sqlalchemy import select

        from metaseed_hub.models import SeekConnection

        await _save_settings("https://seek.example.org", self._two_projects)

        app = create_app()
        a, b, c = _signed_in({"seek"})
        with a, b, c:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get(PROFILE)
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

    async def test_a_profile_yields_its_templates(self, dataset, app_db) -> None:
        response = await _get("/hub/seek/templates/seek-ready-template/3.0", {"seek"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "isa-templates.json" in response.headers["content-disposition"]
        assert response.json()["data"]

    async def test_an_unknown_profile_is_404(self, dataset, app_db) -> None:
        response = await _get("/hub/seek/templates/no-such-profile/1.0", {"seek"})
        assert response.status_code == 404

    async def test_it_needs_the_feature(self, dataset, app_db) -> None:
        response = await _get("/hub/seek/templates/seek-ready-template/3.0", set())
        assert response.status_code == 404

    async def test_the_panel_offers_the_download(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}", {"seek"})).text
        assert 'data-testid="btn-seek-templates"' in html


class TestThePanelOnlyAppearsWhereItWorks:
    """The panel showed on every dataset, including ENA ones, where a push has
    nothing to hang records on: SEEK builds everything under an Investigation,
    and only a profile declaring that role has an ISA shape to map."""

    def test_the_seek_ready_template_qualifies(self) -> None:
        from metaseed_hub.ui.routes.seek import profile_supports_seek

        assert profile_supports_seek("seek-ready-template", "3.0")

    @pytest.mark.parametrize(
        "profile,version",
        [("ena", "1.0"), ("pride", "1.0"), ("miappe", "1.2"), ("darwin-core", "1.0")],
    )
    def test_a_profile_without_isa_roles_does_not(self, profile: str, version: str) -> None:
        from metaseed_hub.ui.routes.seek import profile_supports_seek

        assert not profile_supports_seek(profile, version)

    def test_an_unknown_profile_does_not_raise(self) -> None:
        from metaseed_hub.ui.routes.seek import profile_supports_seek

        assert not profile_supports_seek("no-such-profile", "9.9")

    async def test_an_ena_dataset_shows_no_seek_panel(self, session, app_db) -> None:
        from metaseed_hub.ui.dependencies import tenant_slug_for

        tenant = make_tenant(slug=tenant_slug_for("kc-1"))
        session.add(tenant)
        await session.flush()
        session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
        ena = make_dataset(tenant=tenant, profile="ena", version="1.0")
        session.add(ena)
        await session.commit()

        html = (await _get(f"/hub/datasets/{ena.id}", {"seek"})).text
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
        a, b, c = _signed_in({"seek"})
        with a, b, c:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/hub/seek/datasets/{ena.id}/push", data={})

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
        a, b, c = _signed_in({"seek"})
        with a, b, c, patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.return_value = {}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/hub/seek/datasets/{dataset.id}/check", data={})

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
        a, b, c = _signed_in({"seek"})
        with a, b, c, patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.return_value = installed
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/hub/seek/datasets/{dataset.id}/check", data={})

        assert "Ready" in response.text

    async def test_a_down_instance_says_so_rather_than_blaming_templates(
        self, dataset, app_db, session
    ) -> None:
        await _save_settings("https://seek.example.org", _working)

        app = create_app()
        a, b, c = _signed_in({"seek"})
        with a, b, c, patch("metaseed.seek.client_from_settings") as factory:
            factory.return_value.template_ids_by_title.side_effect = httpx.HTTPStatusError(
                "503",
                request=httpx.Request("GET", "https://seek.example.org/templates"),
                response=httpx.Response(503),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/hub/seek/datasets/{dataset.id}/check", data={})

        assert "not serving SEEK" in response.text

    async def test_the_panel_offers_it(self, dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{dataset.id}", {"seek"})).text
        assert 'data-testid="btn-seek-check"' in html
