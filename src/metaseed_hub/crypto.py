"""Encrypting secrets the hub must be able to use again.

API tokens the hub *issues* are stored as hashes — they only need checking.
Credentials for external services (a SEEK API key) must be sent onward, so a
hash is useless; they are encrypted at rest instead, with a key derived from
``SECRET_KEY``. The admin dashboard already warns when the insecure development
secret is in use, and that warning covers this too.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from metaseed_hub.config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    """Encrypt ``value`` for storage."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(stored: str) -> str | None:
    """The original secret, or ``None`` if it cannot be recovered.

    ``None`` rather than an exception: the one way this fails in practice is
    ``SECRET_KEY`` having changed since the secret was stored, and the remedy —
    re-entering the credential — is the caller's message to give.
    """
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return None
