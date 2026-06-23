"""Tests for application settings loading."""

import pytest

from metaseed_hub.config import Settings


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
