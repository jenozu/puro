"""Offline configuration tests: no SAP connection, no carrier credentials."""
from __future__ import annotations

import pytest

from puro.config import AppConfig, ConfigurationError, redact_text


def test_defaults_are_development_and_no_credentials_needed(monkeypatch):
    for key in (
        "PUROLATOR_ENV", "PUROLATOR_API_KEY", "PUROLATOR_API_PASSWORD",
        "PUROLATOR_ACCOUNT_NUMBER", "PURO_DATA_DIR", "PURO_LABELS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = AppConfig.from_environment(dotenv=False)
    assert settings.environment == "development"
    assert settings.data_dir.as_posix() == "local_data"
    assert not settings.api_key


def test_environment_is_strictly_validated(monkeypatch):
    monkeypatch.setenv("PUROLATOR_ENV", "live!!!")
    with pytest.raises(ConfigurationError, match="PUROLATOR_ENV"):
        AppConfig.from_environment(dotenv=False)


def test_secret_values_cannot_appear_in_repr_or_readiness_errors(monkeypatch):
    monkeypatch.setenv("PUROLATOR_ENV", "development")
    monkeypatch.setenv("PUROLATOR_API_KEY", "an-example-secret-not-real")
    monkeypatch.setenv("PUROLATOR_API_PASSWORD", "an-example-password-not-real")
    monkeypatch.setenv("PUROLATOR_ACCOUNT_NUMBER", "999999999999")
    settings = AppConfig.from_environment(dotenv=False)
    shown = repr(settings) + repr(settings.missing_shipping_settings())
    for secret in (
        "an-example-secret-not-real", "an-example-password-not-real", "999999999999"
    ):
        assert secret not in shown
    raw = "key an-example-secret-not-real / password an-example-password-not-real"
    assert "[REDACTED]" in redact_text(raw, settings)
    assert "an-example-secret-not-real" not in redact_text(raw, settings)


def test_config_validation_does_not_make_network_calls(monkeypatch):
    monkeypatch.setenv("PUROLATOR_ENV", "production")
    settings = AppConfig.from_environment(dotenv=False)
    assert settings.environment == "production"
    assert "DEFAULT_SENDER_NAME" in settings.missing_shipping_settings()
