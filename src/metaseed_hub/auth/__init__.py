"""Keycloak OIDC authentication integration."""

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

    keycloak_id: str
    email: str
    name: str
    roles: list[str]
    tenant_id: str | None = None


class KeycloakAuth:
    """Keycloak OIDC authentication handler."""

    def __init__(self, settings: Settings) -> None:
        """Initialize with settings.

        Args:
            settings: Application settings containing Keycloak configuration.
        """
        self._settings = settings
        self._jwks: dict[str, list[dict[str, str]]] | None = None

    async def get_jwks(self) -> dict[str, list[dict[str, str]]]:
        """Fetch JWKS from Keycloak.

        Returns:
            JWKS dictionary containing public keys.

        Raises:
            HTTPException: If JWKS cannot be fetched.
        """
        if self._jwks is not None:
            return self._jwks

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._settings.keycloak_jwks_url)
                response.raise_for_status()
                self._jwks = response.json()
                return self._jwks
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Failed to fetch JWKS: {e}",
                ) from e

    async def verify_token(self, token: str) -> TokenUser:
        """Verify JWT token and extract user information.

        Args:
            token: JWT access token from Keycloak.

        Returns:
            TokenUser with extracted user information.

        Raises:
            HTTPException: If token is invalid or expired.
        """
        try:
            jwks = await self.get_jwks()

            # Decode without verification first to get the key ID
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            # Find the matching key
            rsa_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    rsa_key = key
                    break

            if rsa_key is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unable to find appropriate key",
                )

            # Verify and decode the token
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                audience=self._settings.keycloak_client_id,
                issuer=self._settings.keycloak_issuer,
            )

            # Extract user information
            return TokenUser(
                keycloak_id=payload.get("sub", ""),
                email=payload.get("email", ""),
                name=payload.get("name", payload.get("preferred_username", "")),
                roles=payload.get("realm_access", {}).get("roles", []),
            )

        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            ) from e


_auth_instance: KeycloakAuth | None = None


def get_keycloak_auth(settings: Settings = Depends(get_settings)) -> KeycloakAuth:
    """Get or create KeycloakAuth instance.

    Args:
        settings: Application settings.

    Returns:
        KeycloakAuth instance.
    """
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = KeycloakAuth(settings)
    return _auth_instance


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: Annotated[KeycloakAuth, Depends(get_keycloak_auth)],
) -> TokenUser:
    """FastAPI dependency to get the current authenticated user.

    Args:
        credentials: HTTP Bearer credentials from request.
        auth: KeycloakAuth instance.

    Returns:
        TokenUser extracted from the JWT token.

    Raises:
        HTTPException: If authentication fails.
    """
    return await auth.verify_token(credentials.credentials)


security_optional = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_optional)],
    auth: Annotated[KeycloakAuth, Depends(get_keycloak_auth)],
) -> TokenUser | None:
    """FastAPI dependency to get the current user if authenticated.

    Args:
        credentials: Optional HTTP Bearer credentials.
        auth: KeycloakAuth instance.

    Returns:
        TokenUser if authenticated, None otherwise.
    """
    if credentials is None:
        return None
    try:
        return await auth.verify_token(credentials.credentials)
    except HTTPException:
        return None


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
    auth = KeycloakAuth(settings)
    return await auth.verify_token(token)


__all__ = [
    "KeycloakAuth",
    "TokenUser",
    "get_current_user",
    "get_current_user_optional",
    "get_keycloak_auth",
    "verify_token",
]
