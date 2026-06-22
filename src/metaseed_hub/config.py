"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub"

    # OIDC (supports Keycloak, SRAM, or any OIDC provider)
    oidc_issuer: str = "http://localhost:7080/realms/metaseed"
    oidc_client_id: str = "metaseed-hub"
    oidc_client_secret: str = "metaseed-hub-dev-secret"
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

    # Admin access (SRAM entitlement or role name)
    admin_role: str = "admin"

    @property
    def effective_issuer(self) -> str:
        """Return the OIDC issuer URL (supports legacy Keycloak config)."""
        if self.oidc_issuer:
            return self.oidc_issuer
        # Fallback to legacy Keycloak config
        if self.keycloak_url and self.keycloak_realm:
            return f"{self.keycloak_url}/realms/{self.keycloak_realm}"
        return ""

    @property
    def effective_client_id(self) -> str:
        """Return the OIDC client ID."""
        return self.oidc_client_id or self.keycloak_client_id

    @property
    def effective_client_secret(self) -> str:
        """Return the OIDC client secret."""
        return self.oidc_client_secret or self.keycloak_client_secret

    @property
    def oidc_discovery_url(self) -> str:
        """Return the OIDC discovery URL."""
        return f"{self.effective_issuer}/.well-known/openid-configuration"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
