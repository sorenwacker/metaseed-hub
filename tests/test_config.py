"""Tests for application settings loading."""

import pytest

from metaseed_hub.config import DEFAULT_SECRET_KEY, Settings


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
