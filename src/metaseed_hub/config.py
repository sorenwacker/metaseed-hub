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

    # Keycloak OIDC
    keycloak_url: str = "http://localhost:7080"
    keycloak_realm: str = "metaseed"
    keycloak_client_id: str = "metaseed-hub"
    keycloak_client_secret: str = "metaseed-hub-dev-secret"

    # Redis
    redis_url: str = "redis://localhost:7379/0"

    # Application
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:7300"]
    app_url: str = "http://localhost:7001"

    @property
    def keycloak_issuer(self) -> str:
        """Return the Keycloak issuer URL."""
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}"

    @property
    def keycloak_jwks_url(self) -> str:
        """Return the Keycloak JWKS URL."""
        return f"{self.keycloak_issuer}/protocol/openid-connect/certs"

    @property
    def keycloak_token_url(self) -> str:
        """Return the Keycloak token URL."""
        return f"{self.keycloak_issuer}/protocol/openid-connect/token"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
