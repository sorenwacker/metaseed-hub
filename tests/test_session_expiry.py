"""An expired session sends the user to the sign-in page, from anywhere.

The hub already refused a dead session on every protected route, and it was
never what the user saw: no response carried a cache directive, so a browser
answered history navigations -- the back button, a restored tab, a reopened
window -- out of its own cache. The dataset list kept drawing itself from a
snapshot long after the session behind it had gone, and a dataset page restored
the same way filled its panels through ``hx-trigger="load"``, each of which now
answered 401, leaving an empty editor. Both are covered here: the refusal, and
the ``no-store`` that makes the browser ask for it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.ui.dependencies import ACCESS_TOKEN_COOKIE
from metaseed_hub.ui.routes.auth import NEXT_COOKIE, REFRESH_TOKEN_COOKIE, RefreshResult
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_tenant, make_user

UI_DIR = Path("src/metaseed_hub/ui")


@pytest.fixture
async def app_db(session):
    """The app-wide connection the routes open their own sessions from."""
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


@pytest.fixture
async def dataset(session: AsyncSession):
    """A dataset owned by ``kc-1``, for the routes that take an id."""
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org"))
    owned = make_dataset(tenant=tenant, profile="ena", version="1.0")
    session.add(owned)
    await session.commit()
    return owned


def _expired_client() -> TestClient:
    """A client whose access token no longer verifies and cannot be refreshed.

    Never entered as a context manager: that runs the application lifespan,
    which connects the configured database over the test one.
    """
    client = TestClient(create_app())
    client.cookies.set(ACCESS_TOKEN_COOKIE, "expired.jwt.token")
    return client


def _client() -> TestClient:
    """A plain client, for the sign-in flow and for authenticated responses."""
    return TestClient(create_app())


def _user() -> TokenUser:
    return TokenUser(sub="kc-1", email="u@example.org", name="U", roles=[], entitlements=[])


# --- the refusal ----------------------------------------------------------


async def test_a_page_request_with_a_dead_session_lands_on_sign_in(dataset, app_db) -> None:
    client = _expired_client()
    response = client.get(f"/hub/datasets/{dataset.id}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == (
        f"/hub/auth/login?next=%2Fhub%2Fdatasets%2F{dataset.id}"
    )


async def test_a_panel_with_a_dead_session_redirects_the_whole_page(dataset, app_db) -> None:
    """The dataset page loads its panels through HTMX; a dead one must not
    simply fail to fill in. htmx acts on HX-Redirect before it looks at the
    status, so the header is what turns a 401 into a sign-in page."""
    client = _expired_client()
    response = client.get(
        f"/hub/datasets/{dataset.id}/tree",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/hub/datasets/{dataset.id}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == (
        f"/hub/auth/login?next=%2Fhub%2Fdatasets%2F{dataset.id}"
    )


async def test_an_edit_with_a_dead_session_redirects_instead_of_failing_quietly(
    dataset, app_db
) -> None:
    """Every browser mutation funnels through ``get_dataset_state_for_mutation``,
    which refused with a bare 401: htmx logged it to the console and the page
    sat there, apparently working, saving nothing."""
    client = _expired_client()
    response = client.post(
        f"/hub/datasets/{dataset.id}/table/node-1/samples/row",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/hub/datasets/{dataset.id}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.headers["HX-Redirect"].startswith("/hub/auth/login?next=")


async def test_a_json_endpoint_says_where_to_sign_in(app_db) -> None:
    """The ontology lookups are read by ``fetch``, which follows a 302 to the
    identity provider and reports an opaque failure. They keep the 401 and name
    the sign-in page in the body instead."""
    client = _expired_client()
    response = client.get(
        "/api/ontology/search",
        params={"q": "leaf"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json()["login_url"].startswith("/hub/auth/login")


_RESPONSE_BUILDERS = frozenset(
    {
        "HTTPException",
        "Response",
        "JSONResponse",
        "HTMLResponse",
        "PlainTextResponse",
    }
)


def _refuses_with_401(node: ast.AST) -> bool:
    """Whether ``node`` builds a 401 response of its own."""
    if not isinstance(node, ast.Call):
        return False
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    if name not in _RESPONSE_BUILDERS:
        return False
    return any(
        keyword.arg == "status_code"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 401
        for keyword in node.keywords
    )


def test_no_ui_route_refuses_a_session_on_its_own() -> None:
    """A 401 written by hand is one that forgets the redirect header, which is
    the whole failure this module is about. ``AuthRequiredError`` is the only
    way to report a missing session, and ``handle_auth_required_error`` the only
    place a 401 response is built."""
    allowed = {UI_DIR / "dependencies.py"}
    offenders: list[str] = []
    scanned = 0

    for path in UI_DIR.rglob("*.py"):
        if path in allowed:
            continue
        scanned += 1
        tree = ast.parse(path.read_text())
        offenders += [f"{path}:{node.lineno}" for node in ast.walk(tree) if _refuses_with_401(node)]

    assert scanned, "no modules scanned; this gate would pass vacuously"
    assert offenders == [], f"401 written outside the auth handler: {offenders}"


# --- the reason the refusal was never seen --------------------------------


async def test_an_authenticated_page_is_never_cached(dataset, app_db) -> None:
    """Without this the browser answers the back button itself, and the user
    reads a dataset list that the session behind it can no longer open."""
    client = _client()
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_user()),
    ):
        response = client.get(f"/hub/datasets/{dataset.id}")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]


async def test_the_sign_in_redirect_is_never_cached(dataset, app_db) -> None:
    client = _expired_client()
    response = client.get(f"/hub/datasets/{dataset.id}", follow_redirects=False)

    assert "no-store" in response.headers["cache-control"]


async def test_static_assets_stay_cacheable(app_db) -> None:
    """Marking the stylesheets and scripts no-store would re-download them on
    every page; they carry no session."""
    client = _client()
    response = client.get("/hub/hub-static/js/hub.js")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get("cache-control", "")


def test_the_page_redirects_itself_when_a_request_is_refused() -> None:
    """The backstop for a request that answers 401 without the header: a plain
    ``fetch`` cannot act on HX-Redirect, and htmx cannot act on a route that
    forgot it."""
    source = Path("src/metaseed_hub/ui/static/js/hub.js").read_text()

    assert "401" in source
    assert "/hub/auth/login" in source


# --- returning to where the user was --------------------------------------


def _fake_oidc_config() -> dict[str, Any]:
    return {
        "authorization_endpoint": "https://idp.example/auth",
        "token_endpoint": "https://idp.example/token",
        "issuer": "https://idp.example",
    }


async def test_signing_in_returns_to_the_page_that_was_refused(app_db) -> None:
    """The callback URL is registered with the identity provider and cannot
    carry a parameter, so where the user was going travels in a cookie."""
    with patch(
        "metaseed_hub.ui.routes.auth.get_oidc_config",
        AsyncMock(return_value=_fake_oidc_config()),
    ):
        client = _client()
        login = client.get(
            "/hub/auth/login",
            params={"next": "/hub/datasets/abc"},
            follow_redirects=False,
        )

    assert login.status_code == 302
    assert login.headers["location"].startswith("https://idp.example/auth?")
    # http.cookies quotes a value containing a slash; the callback unquotes it.
    assert login.cookies[NEXT_COOKIE].strip('"') == "/hub/datasets/abc"


async def test_a_foreign_next_never_reaches_the_callback(app_db) -> None:
    with patch(
        "metaseed_hub.ui.routes.auth.get_oidc_config",
        AsyncMock(return_value=_fake_oidc_config()),
    ):
        client = _client()
        login = client.get(
            "/hub/auth/login",
            params={"next": "https://evil.example/steal"},
            follow_redirects=False,
        )

    assert NEXT_COOKIE not in login.cookies


async def test_a_completed_sign_in_lands_on_the_refused_page(app_db) -> None:
    """End to end: the exchange succeeds and the user arrives where they were
    going, not on the default landing page."""
    tokens = {"access_token": "fresh", "refresh_token": "fresh-refresh", "expires_in": 300}

    class _Exchange:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return tokens

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _Exchange:
            return _Exchange()

    with (
        patch(
            "metaseed_hub.ui.routes.auth.get_oidc_config",
            AsyncMock(return_value=_fake_oidc_config()),
        ),
        patch("metaseed_hub.ui.routes.auth.httpx.AsyncClient", _Client),
        patch("metaseed_hub.auth.verify_token", AsyncMock(return_value=_user())),
    ):
        client = _client()
        client.cookies.set("metaseed_oauth_state", "state-1")
        client.cookies.set(NEXT_COOKIE, "/hub/datasets/abc")
        response = client.get(
            "/hub/auth/callback",
            params={"code": "good-code", "state": "state-1"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/hub/datasets/abc"


def test_a_stored_next_wins_over_the_default_landing() -> None:
    from metaseed_hub.ui.routes.auth import _next_after_login

    assert _next_after_login("/hub/datasets/abc", default="/hub/") == "/hub/datasets/abc"


def test_a_foreign_next_is_refused() -> None:
    """``next`` reaches the callback from the query string, so an attacker can
    write it. Anything that is not a path on this hub falls back."""
    from metaseed_hub.ui.routes.auth import _next_after_login

    for hostile in (
        "https://evil.example/steal",
        "//evil.example/steal",
        "http://evil.example",
        "/../etc/passwd",
        "javascript:alert(1)",
        "",
        None,
    ):
        assert _next_after_login(hostile, default="/hub/home") == "/hub/home", hostile


# --- the dead cookies -----------------------------------------------------


async def test_a_refused_refresh_clears_the_session(dataset, app_db) -> None:
    """The issuer said the refresh token is no good: the session is over, and
    the browser should stop presenting a credential that has been discarded."""
    client = _expired_client()
    client.cookies.set(REFRESH_TOKEN_COOKIE, "dead-refresh-token")
    with patch(
        "metaseed_hub.ui.app.refresh_access_token",
        AsyncMock(return_value=RefreshResult(tokens=None, rejected=True)),
    ):
        response = client.get(f"/hub/datasets/{dataset.id}", follow_redirects=False)

    cleared = [h for h in response.headers.get_list("set-cookie") if "Max-Age=0" in h]
    assert any(ACCESS_TOKEN_COOKIE in h for h in cleared), response.headers.get_list("set-cookie")
    assert any(REFRESH_TOKEN_COOKIE in h for h in cleared)


async def test_an_unreachable_issuer_does_not_end_the_session(dataset, app_db) -> None:
    """An outage at the identity provider is not a verdict on the user's
    session. Clearing the cookies would sign everyone out for the duration of
    someone else's downtime, and they could not sign back in either."""
    client = _expired_client()
    client.cookies.set(REFRESH_TOKEN_COOKIE, "still-good-refresh-token")
    with patch(
        "metaseed_hub.ui.app.refresh_access_token",
        AsyncMock(return_value=RefreshResult(tokens=None, rejected=False)),
    ):
        response = client.get(f"/hub/datasets/{dataset.id}", follow_redirects=False)

    cleared = [h for h in response.headers.get_list("set-cookie") if "Max-Age=0" in h]
    assert not any(REFRESH_TOKEN_COOKIE in h for h in cleared)


def test_a_backslash_destination_is_not_followed() -> None:
    # Browsers normalise ``/\\evil.com`` to ``//evil.com`` -- a different origin
    # -- so the check that refuses ``//`` has to refuse this spelling too.
    from metaseed_hub.ui.routes.auth import _next_after_login

    assert _next_after_login("/hub/\\evil.com/x", default="/hub/") == "/hub/"
    assert _next_after_login("/hub\\evil.com", default="/hub/") == "/hub/"
