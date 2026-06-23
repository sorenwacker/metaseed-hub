"""CSRF-posture tests for the 2026-06-22 review (M3, L2).

The dataset member-management routes and the entity validate POST previously
skipped the CSRF check their sibling mutating routes enforce. These verify that a
request without a valid CSRF token is rejected with HTTP 403 before any work
happens. They need no database: the CSRF guard runs first.
"""

from unittest.mock import Mock

import pytest

from metaseed_hub.ui.routes import entity as entity_module
from metaseed_hub.ui.routes.dataset import members as members_module


def _no_csrf_request() -> Mock:
    """A request mock carrying neither a CSRF cookie nor header."""
    request = Mock()
    request.cookies = {}
    request.headers = {}
    return request


@pytest.mark.asyncio
async def test_add_dataset_member_rejects_missing_csrf() -> None:
    """Adding a member without a CSRF token is rejected."""
    response = await members_module.add_dataset_member(
        request=_no_csrf_request(),
        dataset_id="ds",
        session=Mock(),
        user=Mock(),
        email="a@example.com",
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_member_role_rejects_missing_csrf() -> None:
    """Changing a member role without a CSRF token is rejected."""
    response = await members_module.update_dataset_member_role(
        request=_no_csrf_request(),
        dataset_id="ds",
        user_id="u",
        session=Mock(),
        user=Mock(),
        role="owner",
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_rejects_missing_csrf() -> None:
    """Removing a member without a CSRF token is rejected."""
    response = await members_module.remove_dataset_member(
        request=_no_csrf_request(),
        dataset_id="ds",
        user_id="u",
        session=Mock(),
        user=Mock(),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_entity_validate_rejects_missing_csrf() -> None:
    """The entity validate POST without a CSRF token is rejected."""
    response = await entity_module.dataset_entity_validate(
        request=_no_csrf_request(),
        dataset_id="ds",
        session=Mock(),
        user=Mock(),
    )
    assert response.status_code == 403


class TestCsrfTokenSigning:
    """The CSRF token is signed with the application secret key.

    Signing lets the server recognise tokens it issued, so a forged or fixated
    cookie is rejected even when it is also submitted in the header.
    """

    @staticmethod
    def _request(
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Mock:
        request = Mock()
        request.cookies = cookies or {}
        request.headers = headers or {}
        return request

    def test_issued_token_is_signed_and_validates(self) -> None:
        from metaseed_hub.ui.helpers import (
            CSRF_TOKEN_COOKIE,
            get_or_create_csrf_token,
            validate_csrf_token,
        )

        issued = get_or_create_csrf_token(self._request())
        assert "." in issued  # carries a signature segment

        req = self._request(
            cookies={CSRF_TOKEN_COOKIE: issued},
            headers={"X-CSRF-Token": issued},
        )
        assert validate_csrf_token(req) is True

    def test_existing_signed_token_is_reused(self) -> None:
        from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token

        issued = get_or_create_csrf_token(self._request())
        reused = get_or_create_csrf_token(self._request(cookies={CSRF_TOKEN_COOKIE: issued}))
        assert reused == issued

    def test_unsigned_cookie_is_rejected(self) -> None:
        from metaseed_hub.ui.helpers import (
            CSRF_TOKEN_COOKIE,
            get_or_create_csrf_token,
            validate_csrf_token,
        )

        forged = "forged-token-without-signature"
        req = self._request(
            cookies={CSRF_TOKEN_COOKIE: forged},
            headers={"X-CSRF-Token": forged},
        )
        assert validate_csrf_token(req) is False
        # A fresh signed token is issued rather than trusting the forged value.
        assert get_or_create_csrf_token(req) != forged

    def test_tampered_signature_is_rejected(self) -> None:
        from metaseed_hub.ui.helpers import (
            CSRF_TOKEN_COOKIE,
            get_or_create_csrf_token,
            validate_csrf_token,
        )

        issued = get_or_create_csrf_token(self._request())
        token, _, _signature = issued.rpartition(".")
        tampered = f"{token}.deadbeef"
        req = self._request(
            cookies={CSRF_TOKEN_COOKIE: tampered},
            headers={"X-CSRF-Token": tampered},
        )
        assert validate_csrf_token(req) is False
