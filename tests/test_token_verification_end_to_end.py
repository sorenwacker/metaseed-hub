"""Access tokens are verified end to end: signature, algorithm, expiry, audience.

The hub verified tokens with python-jose, whose algorithm-confusion guard is
bypassed by a DER-encoded public key (GHSA, critical, no patched release).
These tests sign real tokens, so they hold whichever JWT library verifies them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from metaseed_hub.auth import OIDCAuth

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://idp.example.org/realms/test"
AUDIENCE = "metaseed-hub"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk() -> dict[str, str]:
    numbers = _KEY.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": "k1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64(numbers.n),
        "e": _b64(numbers.e),
    }


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "email": "person@example.org",
        "eduperson_entitlement": ["urn:example:group"],
    }
    claims.update(overrides)
    return claims


def _segment(data: dict[str, Any]) -> bytes:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")


def _auth() -> OIDCAuth:
    settings = SimpleNamespace(effective_client_id=AUDIENCE, effective_issuer=ISSUER)
    auth = OIDCAuth(settings=settings)  # type: ignore[arg-type]
    auth.get_oidc_config = AsyncMock(return_value={"issuer": ISSUER})  # type: ignore[method-assign]
    auth._get_signing_key = AsyncMock(return_value=_jwk())  # type: ignore[method-assign]
    return auth


@pytest.mark.asyncio
async def test_a_validly_signed_token_is_accepted() -> None:
    token = jwt.encode(_claims(), _KEY, algorithm="RS256", headers={"kid": "k1"})

    user = await _auth().verify_token(token)

    assert user.sub == "user-1"
    assert user.entitlements == ["urn:example:group"]


@pytest.mark.asyncio
async def test_a_token_signed_with_the_public_key_as_hmac_secret_is_refused() -> None:
    """The algorithm-confusion attack: the public key is public, so HS256 over it
    lets anyone mint tokens."""
    pem = _KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _segment({"alg": "HS256", "kid": "k1"})
    body = _segment(_claims())
    signature = base64.urlsafe_b64encode(
        hmac.new(pem, header + b"." + body, hashlib.sha256).digest()
    ).rstrip(b"=")
    forged = (header + b"." + body + b"." + signature).decode()

    with pytest.raises(HTTPException) as refused:
        await _auth().verify_token(forged)
    assert refused.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        _claims(exp=int(time.time()) - 60),
        _claims(aud="someone-else"),
        _claims(iss="https://evil.example.org"),
    ],
    ids=["expired", "wrong-audience", "wrong-issuer"],
)
async def test_an_invalid_token_is_refused(claims: dict[str, Any]) -> None:
    token = jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})

    with pytest.raises(HTTPException) as refused:
        await _auth().verify_token(token)
    assert refused.value.status_code == 401


def test_python_jose_is_not_a_dependency() -> None:
    """python-jose has an unpatched critical advisory; it must not come back."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = " ".join(project["dependencies"]).lower()
    assert "python-jose" not in declared
    assert not [
        p
        for p in (ROOT / "src").rglob("*.py")
        if "from jose" in p.read_text() or "import jose" in p.read_text()
    ]
