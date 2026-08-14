"""OIDC authentication integration (supports Keycloak, SRAM, and other providers)."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.config import Settings, get_settings
from metaseed_hub.database import get_session
from metaseed_hub.tokens import TOKEN_PREFIX, authenticate_token

security = HTTPBearer()

ALLOWED_SIGNING_ALGORITHMS: tuple[str, ...] = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
)
"""The JWT algorithms token verification accepts. Ours, not the IdP's.

The discovery document's ``id_token_signing_alg_values_supported`` is the
issuer describing itself, and it was passed straight into ``jwt.decode`` — so
an IdP advertising ``HS256`` would have verification treat the public RSA key,
a public value, as a shared secret, letting anyone mint valid tokens. The
allowed set is fixed and asymmetric only.
"""


@dataclass
class TokenUser:
    """User information extracted from JWT token."""

    sub: str  # Subject ID (provider-agnostic)
    email: str
    name: str
    roles: list[str]
    # Group membership as the identity provider states it, always from
    # ``eduperson_entitlement`` and never from ``roles`` -- ``roles`` means a
    # Keycloak realm role in development and a SRAM group URN in production, so
    # anything reading it behaves differently per issuer. Read per request from
    # the verified token rather than stored, so it cannot go stale.
    entitlements: list[str] = dc_field(default_factory=list)

    @property
    def keycloak_id(self) -> str:
        """Alias for backwards compatibility."""
        return self.sub


def _entitlement_list(payload: dict[str, Any]) -> list[str]:
    """``eduperson_entitlement`` as a list, whatever shape the IdP sent.

    A single-valued claim arrives as a bare string rather than a list, and an
    IdP may omit it entirely.
    """
    raw = payload.get("eduperson_entitlement") or []
    if isinstance(raw, str):
        return [raw]
    return [value for value in raw if isinstance(value, str)]


class OIDCAuth:
    """OIDC authentication handler using discovery."""

    def __init__(self, settings: Settings) -> None:
        """Initialize with settings.

        Args:
            settings: Application settings containing OIDC configuration.
        """
        self._settings = settings
        self._jwks: dict[str, list[dict[str, str]]] | None = None
        self._oidc_config: dict[str, Any] | None = None
        self._userinfo_cache: dict[str, tuple[float, list[str]]] = {}

    async def get_oidc_config(self) -> dict[str, Any]:
        """Fetch OIDC discovery document.

        Returns:
            OIDC configuration dictionary.

        Raises:
            HTTPException: If discovery document cannot be fetched.
        """
        if self._oidc_config is not None:
            return self._oidc_config

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._settings.oidc_discovery_url)
                response.raise_for_status()
                self._oidc_config = response.json()
                return self._oidc_config
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Failed to fetch OIDC discovery: {e}",
                ) from e

    async def get_jwks(self) -> dict[str, list[dict[str, str]]]:
        """Fetch JWKS from OIDC provider.

        Returns:
            JWKS dictionary containing public keys.

        Raises:
            HTTPException: If JWKS cannot be fetched.
        """
        if self._jwks is not None:
            return self._jwks

        oidc_config = await self.get_oidc_config()
        jwks_uri = oidc_config.get("jwks_uri")
        if not jwks_uri:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC provider does not provide jwks_uri",
            )

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(jwks_uri)
                response.raise_for_status()
                self._jwks = response.json()
                return self._jwks
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Failed to fetch JWKS: {e}",
                ) from e

    @staticmethod
    def _find_key(jwks: dict[str, list[dict[str, str]]], kid: str | None) -> dict[str, str] | None:
        """Find the signing key matching ``kid`` in a JWKS document."""
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
        return None

    async def _get_signing_key(self, kid: str | None) -> dict[str, str]:
        """Resolve the signing key for ``kid``, refreshing the JWKS if needed.

        If no key matches (e.g. after the provider rotated signing keys), the
        cached JWKS is invalidated and fetched once more before giving up, so a
        long-lived process does not reject valid tokens until restart.

        Raises:
            HTTPException: 401 if no matching key is found after a refresh.
        """
        jwks = await self.get_jwks()
        rsa_key = self._find_key(jwks, kid)
        if rsa_key is None:
            self._jwks = None
            jwks = await self.get_jwks()
            rsa_key = self._find_key(jwks, kid)
        if rsa_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to find appropriate key",
            )
        return rsa_key

    async def _userinfo_entitlements(self, token: str) -> list[str]:
        """Entitlements from the userinfo endpoint, cached for the token's life.

        Returns an empty list rather than raising when the endpoint is
        unreachable: entitlements gate optional features, and a hiccup there
        must not turn into a failed login.
        """
        import hashlib
        import time

        key = hashlib.sha256(token.encode()).hexdigest()
        cached = self._userinfo_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        try:
            config = await self.get_oidc_config()
            endpoint = config.get("userinfo_endpoint")
            if not endpoint:
                return []
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5.0,
                )
            if response.status_code != 200:
                return []
            entitlements = _entitlement_list(response.json())
        except Exception:
            return []

        # Bounded cache: userinfo is one network call per unseen token, and a
        # token is presented on every request for its whole lifetime.
        if len(self._userinfo_cache) > 512:
            self._userinfo_cache.clear()
        self._userinfo_cache[key] = (time.monotonic() + 300, entitlements)
        return entitlements

    async def verify_token(self, token: str) -> TokenUser:
        """Verify JWT token and extract user information.

        Args:
            token: JWT access token from OIDC provider.

        Returns:
            TokenUser with extracted user information.

        Raises:
            HTTPException: If token is invalid or expired.
        """
        try:
            oidc_config = await self.get_oidc_config()

            # Decode without verification first to get the key ID
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            rsa_key = await self._get_signing_key(kid)

            # Verify and decode the token. The algorithm list is OURS
            # (asymmetric only), never the discovery document's — see
            # ALLOWED_SIGNING_ALGORITHMS for why trusting the issuer's own
            # list would let it downgrade verification to symmetric.
            issuer = oidc_config.get("issuer", self._settings.effective_issuer)
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=list(ALLOWED_SIGNING_ALGORITHMS),
                audience=self._settings.effective_client_id,
                issuer=issuer,
            )

            # Roles are Keycloak realm roles and nothing else. Group
            # membership has its own field below; the old fallback that poured
            # entitlements into roles made the list mean a different thing per
            # issuer, and admin checks inherited the ambiguity.
            roles = payload.get("realm_access", {}).get("roles", [])

            entitlements = _entitlement_list(payload)
            if not entitlements:
                # SRAM does not put eduperson_entitlement in the access token —
                # only the userinfo endpoint carries the full claims. Falling
                # back (cached per token) keeps one code path for both issuers
                # instead of a dev-only feature that dies on deployment.
                entitlements = await self._userinfo_entitlements(token)

            return TokenUser(
                sub=payload.get("sub", ""),
                email=payload.get("email", ""),
                name=payload.get("name", payload.get("preferred_username", "")),
                roles=roles,
                entitlements=entitlements,
            )

        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            ) from e


_auth_instance: OIDCAuth | None = None


def get_oidc_auth(settings: Settings = Depends(get_settings)) -> OIDCAuth:
    """Get or create OIDCAuth instance.

    Args:
        settings: Application settings.

    Returns:
        OIDCAuth instance.
    """
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = OIDCAuth(settings)
    return _auth_instance


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: Annotated[OIDCAuth, Depends(get_oidc_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenUser:
    """FastAPI dependency to get the current authenticated user.

    Accepts either an OIDC access token or a personal access token. A browser
    presents the first; a script or an agent cannot obtain one, because getting
    it requires the interactive sign-in flow. Without the second the REST API is
    reachable only from a browser session, which is why pushing a dataset from
    the ``metaseed`` library had no usable credential.

    Args:
        credentials: HTTP Bearer credentials from request.
        auth: OIDCAuth instance.
        session: Database session, for personal access tokens.

    Returns:
        The authenticated user, whichever credential was presented.

    Raises:
        HTTPException: If neither form authenticates.
    """
    presented = credentials.credentials

    # Checked first and by prefix, so an OIDC failure is never reported for what
    # is plainly a hub token, and a hub token is never sent to the IdP.
    if presented.startswith(TOKEN_PREFIX):
        user = await authenticate_token(session, presented)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That access token is not valid, has expired, or was revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenUser(
            sub=user.keycloak_id,
            email=user.email,
            name=user.display_name or user.email,
            # Personal access tokens carry no roles: they act for the user's own
            # data, and must not confer the admin role an OIDC token can. They
            # also carry no entitlements — those come from the IdP's userinfo
            # endpoint, which only an OIDC token can ask — so features gated on
            # an entitlement are browser-session-only by construction, not by
            # accident.
            roles=[],
        )

    return await auth.verify_token(presented)


async def verify_token(token: str) -> TokenUser:
    """Standalone function to verify a token against the application settings.

    Args:
        token: JWT access token.

    Returns:
        TokenUser extracted from the JWT token.
    """
    # Reuse the shared singleton so the OIDC discovery and JWKS caches are
    # shared across requests instead of refetched on every call. The singleton
    # keeps the settings it was constructed with, so alternate settings cannot
    # be honored here; callers needing a different issuer must construct their
    # own OIDCAuth.
    auth = get_oidc_auth(get_settings())
    return await auth.verify_token(token)


__all__ = [
    "OIDCAuth",
    "TokenUser",
    "get_current_user",
    "get_oidc_auth",
    "verify_token",
]
