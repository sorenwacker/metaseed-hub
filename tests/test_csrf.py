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
