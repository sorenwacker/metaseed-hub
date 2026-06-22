"""OIDC authentication integration (supports Keycloak, SRAM, and other providers)."""

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from metaseed_hub.config import Settings, get_settings

security = HTTPBearer()


@dataclass
class TokenUser:
    """User information extracted from JWT token."""

    sub: str  # Subject ID (provider-agnostic)
    email: str
    name: str
    roles: list[str]

    @property
    def keycloak_id(self) -> str:
        """Alias for backwards compatibility."""
        return self.sub


class OIDCAuth:
    """OIDC authentication handler using discovery."""

    def __init__(self, settings: Settings) -> None:
        """Initialize with settings.

        Args:
            settings: Application settings containing OIDC configuration.
        """
        self._settings = settings
        self._jwks: dict[str, list[dict[str, str]]] | None = None
        self._oidc_config: dict[str, str] | None = None

    async def get_oidc_config(self) -> dict[str, str]:
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

            # Verify and decode the token
            issuer = oidc_config.get("issuer", self._settings.effective_issuer)
            # Support all common signing algorithms
            supported_algs = oidc_config.get(
                "id_token_signing_alg_values_supported",
                ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            )
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=supported_algs,
                audience=self._settings.effective_client_id,
                issuer=issuer,
            )

            # Extract user information (provider-agnostic)
            # Keycloak uses realm_access.roles, SRAM uses eduperson_entitlement
            roles = payload.get("realm_access", {}).get("roles", [])
            if not roles:
                roles = payload.get("eduperson_entitlement", [])

            return TokenUser(
                sub=payload.get("sub", ""),
                email=payload.get("email", ""),
                name=payload.get("name", payload.get("preferred_username", "")),
                roles=roles,
            )

        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            ) from e


# Backwards compatibility aliases
KeycloakAuth = OIDCAuth

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


# Backwards compatibility alias
get_keycloak_auth = get_oidc_auth


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: Annotated[OIDCAuth, Depends(get_oidc_auth)],
) -> TokenUser:
    """FastAPI dependency to get the current authenticated user.

    Args:
        credentials: HTTP Bearer credentials from request.
        auth: OIDCAuth instance.

    Returns:
        TokenUser extracted from the JWT token.

    Raises:
        HTTPException: If authentication fails.
    """
    return await auth.verify_token(credentials.credentials)


async def verify_token(token: str, settings: Settings | None = None) -> TokenUser:
    """Standalone function to verify a token.

    Args:
        token: JWT access token.
        settings: Optional settings, uses default if not provided.

    Returns:
        TokenUser extracted from the JWT token.
    """
    if settings is None:
        settings = get_settings()
    # Reuse the shared singleton so the OIDC discovery and JWKS caches are
    # shared across requests instead of refetched on every call.
    auth = get_oidc_auth(settings)
    return await auth.verify_token(token)


__all__ = [
    "KeycloakAuth",  # Backwards compatibility alias
    "OIDCAuth",
    "TokenUser",
    "get_current_user",
    "get_keycloak_auth",  # Backwards compatibility alias
    "get_oidc_auth",
    "verify_token",
]
