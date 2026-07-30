"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Insecure development fallback for ``secret_key``. Production must override it
# via the SECRET_KEY environment variable; the admin dashboard warns when this
# value is still in use.
DEFAULT_SECRET_KEY = "metaseed-hub-dev-secret-key"

# Development OIDC fallbacks, applied only in the ``effective_*`` properties and
# only when neither OIDC_* nor legacy KEYCLOAK_* variables are configured. They
# must not be field defaults: a non-empty ``oidc_*`` default would shadow the
# legacy KEYCLOAK_* configuration, silently authenticating such deployments
# against the localhost development issuer.
DEV_OIDC_ISSUER = "http://localhost:7080/realms/metaseed"
DEV_OIDC_CLIENT_ID = "metaseed-hub"
DEV_OIDC_CLIENT_SECRET = "metaseed-hub-dev-secret"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub"

    # OIDC (supports Keycloak, SRAM, or any OIDC provider). Empty by default so
    # the legacy KEYCLOAK_* variables below can take effect; the effective_*
    # properties fall back to the development defaults when neither is set.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scope: str = "openid email profile"  # Add offline_access eduperson_entitlement for SRAM

    # Legacy Keycloak settings (for backwards compatibility)
    keycloak_url: str = ""
    keycloak_realm: str = ""
    keycloak_client_id: str = ""
    keycloak_client_secret: str = ""

    # Redis
    redis_url: str = "redis://localhost:7379/0"

    # Application
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:7300"]
    app_url: str = "http://localhost:7001"

    # Matomo analytics. Cookieless and first-party, served same-origin at
    # matomo_url so the strict CSP (script-src/connect-src 'self') allows it.
    # Rendered only when matomo_site_id is set, so dev and CI stay clean.
    matomo_url: str = "/matomo/"
    matomo_site_id: str = ""

    # Application secret key. Provided by the deployment environment and used to
    # sign the CSRF token. Declared here so the value is accepted and validated
    # rather than rejected as an unknown environment variable.
    secret_key: str = DEFAULT_SECRET_KEY

    # Admin access (SRAM entitlement or role name)
    admin_role: str = "admin"

    @property
    def using_default_secret_key(self) -> bool:
        """Return True when the insecure development secret key is still in use."""
        return self.secret_key == DEFAULT_SECRET_KEY

    @property
    def effective_issuer(self) -> str:
        """Return the OIDC issuer URL.

        Precedence: OIDC_ISSUER, then legacy KEYCLOAK_URL/KEYCLOAK_REALM, then
        the development default.
        """
        if self.oidc_issuer:
            return self.oidc_issuer
        if self.keycloak_url and self.keycloak_realm:
            return f"{self.keycloak_url}/realms/{self.keycloak_realm}"
        return DEV_OIDC_ISSUER

    @property
    def effective_client_id(self) -> str:
        """Return the OIDC client ID (same precedence as effective_issuer)."""
        return self.oidc_client_id or self.keycloak_client_id or DEV_OIDC_CLIENT_ID

    @property
    def effective_client_secret(self) -> str:
        """Return the OIDC client secret (same precedence as effective_issuer)."""
        return self.oidc_client_secret or self.keycloak_client_secret or DEV_OIDC_CLIENT_SECRET

    @property
    def oidc_discovery_url(self) -> str:
        """Return the OIDC discovery URL."""
        return f"{self.effective_issuer}/.well-known/openid-configuration"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
