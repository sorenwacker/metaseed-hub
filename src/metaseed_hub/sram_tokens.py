"""SRAM application tokens, checked against SRAM's introspection endpoint.

A member creates a token for this application in SRAM; the hub cannot read it
and keeps nothing about it. Each request carrying one is answered by asking
SRAM, with the hub's own introspection credential, whether the token is
active and whom it belongs to. The answer also carries the person's current
``eduperson_entitlement`` groups, so it is as authoritative about their
collaborations as a sign-in. Answers are cached briefly, so a client making
many requests does not ask SRAM for each one.

What SRAM says is reported as SRAM said it. A SRAM that cannot be reached is
*not checked* -- someone else's downtime must not read as a revoked
credential -- and a hub credential SRAM no longer accepts is the
administrator's problem, not the user's.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from metaseed_hub.config import Settings, get_settings
from metaseed_hub.entitlements import ENTITLEMENT_CLAIM

logger = logging.getLogger(__name__)

#: SRAM's own words for an answer that is not a valid token.
REFUSALS = frozenset({"token-expired", "token-unknown", "user-suspended", "token-not-connected"})

#: How long a refusal is remembered, so a client retrying a dead token in a
#: loop does not become a request to SRAM per retry.
_REFUSAL_TTL_SECONDS = 60


class IntrospectionUnavailableError(Exception):
    """SRAM could not be reached, or answered with a server error."""


class IntrospectionCredentialError(Exception):
    """SRAM rejected the hub's own introspection credential."""


@dataclass(frozen=True)
class Introspection:
    """What SRAM said about one token."""

    active: bool
    status: str
    sub: str = ""
    email: str = ""
    name: str = ""
    entitlements: tuple[str, ...] = field(default_factory=tuple)
    expires_at: float | None = None


_cache: dict[str, tuple[float, Introspection]] = {}


def clear_cache() -> None:
    """Forget every cached answer (tests, and a changed credential)."""
    _cache.clear()


def _key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _parse(body: dict[str, Any]) -> Introspection:
    status = str(body.get("status") or ("token-valid" if body.get("active") else "token-unknown"))
    active = bool(body.get("active")) and status == "token-valid"
    # SRAM spells the family name "familiy_name" in its answer; both are read.
    parts = (body.get("given_name"), body.get("familiy_name") or body.get("family_name"))
    name = " ".join(str(part) for part in parts if part)
    claimed = body.get(ENTITLEMENT_CLAIM)
    entitlements = tuple(
        value for value in (claimed if isinstance(claimed, list) else []) if isinstance(value, str)
    )
    exp = body.get("exp")
    return Introspection(
        active=active,
        status=status,
        sub=str(body.get("sub") or ""),
        email=str(body.get("email") or ""),
        name=name or str(body.get("username") or ""),
        entitlements=entitlements,
        expires_at=float(exp) if isinstance(exp, int | float) else None,
    )


async def introspect(
    secret: str, *, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None
) -> Introspection:
    """Ask SRAM about ``secret``, reusing a recent answer.

    Raises:
        IntrospectionCredentialError: SRAM answered 401 or 403 to the hub's
            own credential.
        IntrospectionUnavailableError: SRAM could not be reached or answered with
            a server error; the token is not checked.
    """
    settings = settings or get_settings()
    key = _key(secret)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    client = http_client or httpx.AsyncClient(timeout=10.0)
    owns_client = http_client is None
    try:
        response = await client.post(
            settings.sram_introspection_url,
            data={"token": secret},
            headers={"Authorization": f"Bearer {settings.sram_introspection_token}"},
        )
    except httpx.HTTPError as exc:
        raise IntrospectionUnavailableError(str(exc)) from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code in (401, 403):
        logger.error(
            "SRAM rejected the hub's introspection credential (%s). "
            "Renew SRAM_INTROSPECTION_TOKEN from the application's settings in SRAM.",
            response.status_code,
        )
        raise IntrospectionCredentialError(str(response.status_code))
    if response.status_code >= 500:
        raise IntrospectionUnavailableError(f"SRAM answered {response.status_code}")
    try:
        answer = _parse(response.json())
    except ValueError as exc:
        raise IntrospectionUnavailableError("SRAM answered with something other than JSON") from exc

    ttl = (
        float(settings.sram_introspection_cache_seconds) if answer.active else _REFUSAL_TTL_SECONDS
    )
    if answer.active and answer.expires_at is not None:
        ttl = min(ttl, max(0.0, answer.expires_at - time.time()))
    _cache[key] = (now + ttl, answer)
    return answer
