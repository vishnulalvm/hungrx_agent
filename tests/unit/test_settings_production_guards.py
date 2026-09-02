"""Unit tests for core/config/settings.py's fail-fast production guards
(security review fixes): a weak/default api_secret_key, and a CORS
wildcard-with-credentials configuration, must both refuse to boot when
environment=production rather than silently running insecurely."""

import pytest

from apps.api.app.main import create_app
from core.config.settings import Settings


class TestWeakSecretKeyGuard:
    def test_default_secret_key_is_rejected_in_production(self) -> None:
        with pytest.raises(ValueError, match="api_secret_key"):
            Settings(environment="production", api_secret_key="change-me")

    def test_empty_secret_key_is_rejected_in_production(self) -> None:
        with pytest.raises(ValueError, match="api_secret_key"):
            Settings(environment="production", api_secret_key="")

    def test_short_secret_key_is_rejected_in_production(self) -> None:
        with pytest.raises(ValueError, match="api_secret_key"):
            Settings(environment="production", api_secret_key="too-short")

    def test_strong_secret_key_is_accepted_in_production(self) -> None:
        settings = Settings(
            environment="production",
            api_secret_key="a-genuinely-long-random-secret-key-value-123456",
        )
        assert settings.environment == "production"

    def test_default_secret_key_is_fine_outside_production(self) -> None:
        settings = Settings(environment="development", api_secret_key="change-me")
        assert settings.api_secret_key == "change-me"


class TestCorsWildcardGuard:
    def test_wildcard_cors_is_rejected_in_production(self, monkeypatch) -> None:
        from apps.api.app import main as main_module

        settings = Settings(
            environment="production",
            api_secret_key="a-genuinely-long-random-secret-key-value-123456",
            cors_origins="*",
        )
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)

        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            create_app()

    def test_wildcard_cors_is_fine_outside_production(self, monkeypatch) -> None:
        from apps.api.app import main as main_module

        settings = Settings(environment="development", cors_origins="*")
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)

        create_app()  # must not raise

    def test_explicit_origins_are_fine_in_production(self, monkeypatch) -> None:
        from apps.api.app import main as main_module

        settings = Settings(
            environment="production",
            api_secret_key="a-genuinely-long-random-secret-key-value-123456",
            cors_origins="https://admin.hungrx.example",
        )
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)

        create_app()  # must not raise
