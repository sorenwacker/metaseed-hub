"""A SRAM application token is as alive as the person's place in SRAM.

A hub personal access token outlives the account it was issued to: the hub
never asks the identity provider again, so someone whose institutional login
was withdrawn keeps reaching their account until the token expires or is
revoked. A SRAM application token is checked against SRAM's introspection
endpoint on every request (cached briefly), with the hub's own credential, and
SRAM answers with the person's current groups, so the token stops when SRAM
says so and the collaboration reading is refreshed without a sign-in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub import sram_tokens
from metaseed_hub.bearers import BearerError, resolve_bearer
from metaseed_hub.collaborations import entitled_urns_of
from metaseed_hub.config import get_settings
from tests.factories import make_tenant, make_user

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
GROUP = "urn:mace:surf.nl:sram:group:tudelft:cropxr:phenotyping"
SUB = "sram-subject-1"


def _answer(status: str = "token-valid", **extra: object) -> dict:
    body: dict = {"active": status == "token-valid", "status": status}
    if status == "token-valid":
        body.update(
            {
                "sub": SUB,
                "email": "r@example.org",
                "given_name": "R",
                "familiy_name": "Esearcher",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                "eduperson_entitlement": [GROUP],
            }
        )
    body.update(extra)
    return body


class _Sram:
    """A fake introspection endpoint that counts what it was asked."""

    def __init__(self, response: dict | int | Exception = None) -> None:
        self.response = response if response is not None else _answer()
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        if isinstance(self.response, int):
            return httpx.Response(self.response)
        return httpx.Response(200, json=self.response)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "sram_introspection_token", "hub-introspection-credential")
    sram_tokens.clear_cache()
    yield
    sram_tokens.clear_cache()


async def _account(session: AsyncSession, *, sub: str = SUB) -> None:
    tenant = make_tenant(slug=f"t-{sub}")
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, email="r@example.org", keycloak_id=sub))
    await session.commit()


@pytest.mark.asyncio
async def test_a_valid_token_acts_as_the_account_sram_names(session: AsyncSession) -> None:
    await _account(session)
    sram = _Sram()

    bearer = await resolve_bearer(session, "opaque-sram-token", http_client=sram.client())

    assert bearer.kind == "sram"
    assert bearer.user.keycloak_id == SUB
    assert bearer.token_user.email == "r@example.org"
    assert bearer.token_user.roles == []
    assert GROUP in bearer.token_user.entitlements
    request = sram.requests[0]
    assert request.headers["authorization"] == "Bearer hub-introspection-credential"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert request.content == b"token=opaque-sram-token"


@pytest.mark.asyncio
async def test_a_valid_token_refreshes_the_collaboration_reading(session: AsyncSession) -> None:
    """SRAM's answer carries the current groups: as authoritative as a sign-in."""
    await _account(session)

    bearer = await resolve_bearer(session, "opaque-sram-token", http_client=_Sram().client())

    assert GROUP in await entitled_urns_of(session, bearer.user.id)


@pytest.mark.parametrize(
    "status", ["token-expired", "token-unknown", "user-suspended", "token-not-connected"]
)
@pytest.mark.asyncio
async def test_what_sram_refuses_is_refused_with_its_reason(session: AsyncSession, status) -> None:
    await _account(session)

    with pytest.raises(BearerError) as refused:
        await resolve_bearer(session, "opaque", http_client=_Sram(_answer(status)).client())

    assert refused.value.status_code == 401
    assert status in refused.value.detail


@pytest.mark.asyncio
async def test_a_subject_without_an_account_is_told_to_sign_in_first(session: AsyncSession) -> None:
    with pytest.raises(BearerError) as refused:
        await resolve_bearer(session, "opaque", http_client=_Sram().client())

    assert refused.value.status_code == 401
    assert "sign in" in refused.value.detail.lower()


@pytest.mark.asyncio
async def test_an_unreachable_sram_is_not_checked_not_a_refusal(session: AsyncSession) -> None:
    await _account(session)
    sram = _Sram(httpx.ConnectError("no route"))

    with pytest.raises(BearerError) as refused:
        await resolve_bearer(session, "opaque", http_client=sram.client())

    assert refused.value.status_code == 503
    assert "not checked" in refused.value.detail.lower()


@pytest.mark.asyncio
async def test_a_rejected_hub_credential_is_the_administrators_problem(
    session: AsyncSession, caplog
) -> None:
    await _account(session)

    with caplog.at_level("ERROR"), pytest.raises(BearerError) as refused:
        await resolve_bearer(session, "opaque", http_client=_Sram(401).client())

    assert refused.value.status_code == 503
    assert "SRAM_INTROSPECTION_TOKEN" in caplog.text


@pytest.mark.asyncio
async def test_sram_tokens_are_refused_where_not_enabled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "sram_introspection_token", "")
    sram = _Sram()

    with pytest.raises(BearerError) as refused:
        await resolve_bearer(session, "opaque", http_client=sram.client())

    assert refused.value.status_code == 401
    assert "not enabled" in refused.value.detail.lower()
    assert sram.requests == []


@pytest.mark.asyncio
async def test_an_answer_is_reused_rather_than_asked_for_per_request(session: AsyncSession) -> None:
    await _account(session)
    sram = _Sram()

    await resolve_bearer(session, "opaque", http_client=sram.client())
    await resolve_bearer(session, "opaque", http_client=sram.client())

    assert len(sram.requests) == 1


@pytest.mark.asyncio
async def test_a_hub_token_and_an_oidc_bearer_never_reach_sram(session: AsyncSession) -> None:
    await _account(session)
    sram = _Sram()

    with pytest.raises(BearerError):
        await resolve_bearer(session, "msh_not-a-real-token", http_client=sram.client())
    with pytest.raises(BearerError):
        await resolve_bearer(session, "aaa.bbb.ccc", http_client=sram.client())

    assert sram.requests == []


@pytest.mark.asyncio
async def test_the_rest_api_accepts_a_sram_token(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from fastapi.security import HTTPAuthorizationCredentials

    from metaseed_hub.auth import get_current_user

    await _account(session)
    monkeypatch.setattr("metaseed_hub.bearers.default_http_client", _Sram().client)

    resolved = await get_current_user(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
        auth=Mock(),
        session=session,
    )

    assert resolved.keycloak_id == SUB
    assert GROUP in resolved.entitlements


@pytest.mark.asyncio
async def test_the_mcp_endpoint_accepts_a_sram_token(
    server, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metaseed_hub import mcp as hub_mcp

    await _account(session)
    monkeypatch.setattr("metaseed_hub.bearers.default_http_client", _Sram().client)
    monkeypatch.setattr(hub_mcp, "_bearer_token", lambda: "opaque")

    async with hub_mcp._caller() as (_session, user):
        assert user.keycloak_id == SUB


def test_rest_and_mcp_share_one_bearer_resolver() -> None:
    """Two copies of the by-prefix decision drifted once; the third credential
    must not be added to each."""
    for module in ("auth/__init__.py", "mcp/__init__.py"):
        source = (SRC / module).read_text(encoding="utf-8")
        assert "resolve_bearer(" in source, f"{module} does not use resolve_bearer"
        assert "authenticate_token(" not in source, f"{module} decides a hub token itself"
    owners = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "authenticate_token(" in path.read_text(encoding="utf-8")
    }
    assert owners <= {"tokens.py", "bearers.py"}, owners


def test_the_mcp_docs_no_longer_name_the_hub_token_as_the_only_credential() -> None:
    docs = (SRC.parents[1] / "docs" / "mcp.md").read_text(encoding="utf-8")
    assert "SRAM application token" in docs
    assert json.dumps("msh_your_token_here") not in json.dumps(docs)
