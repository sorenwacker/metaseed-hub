"""Tests for application settings loading."""

import pytest

from metaseed_hub.config import (
    DEFAULT_SECRET_KEY,
    DEV_OIDC_CLIENT_ID,
    DEV_OIDC_CLIENT_SECRET,
    DEV_OIDC_ISSUER,
    Settings,
)


def _settings(**overrides: str) -> Settings:
    """Build Settings with all OIDC-related fields pinned, bypassing the environment.

    Explicit keyword arguments take precedence over environment variables in
    pydantic-settings, so pinning every OIDC field keeps these tests
    deterministic regardless of the developer's shell environment.
    """
    fields: dict[str, str] = {
        "oidc_issuer": "",
        "oidc_client_id": "",
        "oidc_client_secret": "",
        "keycloak_url": "",
        "keycloak_realm": "",
        "keycloak_client_id": "",
        "keycloak_client_secret": "",
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def test_secret_key_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SECRET_KEY from the environment must populate the settings field.

    The production environment supplies SECRET_KEY. The field must be declared
    so the value is accepted and validated; otherwise settings loading raises
    and the deploy aborts at the migration step.
    """
    monkeypatch.setenv("SECRET_KEY", "production-secret-value")

    settings = Settings(_env_file=None)

    assert settings.secret_key == "production-secret-value"


def test_using_default_secret_key_true_for_default() -> None:
    """The flag is set when the insecure development secret key is in use."""
    settings = Settings(_env_file=None, secret_key=DEFAULT_SECRET_KEY)

    assert settings.using_default_secret_key is True


def test_using_default_secret_key_false_when_overridden() -> None:
    """The flag is clear when a real secret key is configured."""
    settings = Settings(_env_file=None, secret_key="a-strong-production-secret")

    assert settings.using_default_secret_key is False


def test_legacy_keycloak_settings_take_effect_when_oidc_unset() -> None:
    """A deployment setting only KEYCLOAK_* variables must reach its own IdP.

    Before the fix, non-empty oidc_* defaults shadowed the legacy fields, so
    such a deployment silently authenticated against the localhost dev issuer.
    """
    settings = _settings(
        keycloak_url="https://kc.example.org",
        keycloak_realm="prod",
        keycloak_client_id="hub-prod",
        keycloak_client_secret="prod-secret",
    )

    assert settings.effective_issuer == "https://kc.example.org/realms/prod"
    assert settings.effective_client_id == "hub-prod"
    assert settings.effective_client_secret == "prod-secret"
    assert settings.oidc_discovery_url == (
        "https://kc.example.org/realms/prod/.well-known/openid-configuration"
    )


def test_oidc_settings_win_over_legacy_keycloak() -> None:
    """When both variable families are set, the OIDC_* values take precedence."""
    settings = _settings(
        oidc_issuer="https://sram.example.org",
        oidc_client_id="hub-sram",
        oidc_client_secret="sram-secret",
        keycloak_url="https://kc.example.org",
        keycloak_realm="prod",
        keycloak_client_id="hub-prod",
        keycloak_client_secret="prod-secret",
    )

    assert settings.effective_issuer == "https://sram.example.org"
    assert settings.effective_client_id == "hub-sram"
    assert settings.effective_client_secret == "sram-secret"


def test_dev_defaults_apply_when_nothing_configured() -> None:
    """With no OIDC configuration at all, the development defaults apply."""
    settings = _settings()

    assert settings.effective_issuer == DEV_OIDC_ISSUER
    assert settings.effective_client_id == DEV_OIDC_CLIENT_ID
    assert settings.effective_client_secret == DEV_OIDC_CLIENT_SECRET
