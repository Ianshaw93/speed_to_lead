"""Tests for configuration management."""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    """Test the Settings configuration class."""

    def test_settings_loads_from_environment(self):
        """Settings should load values from environment variables."""
        env_vars = {
            "HEYREACH_API_KEY": "test_heyreach_key",
            "DEEPSEEK_API_KEY": "test_deepseek_key",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "TELEGRAM_BOT_TOKEN": "test_telegram_token",
            "TELEGRAM_CHAT_ID": "123456789",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
            "SECRET_KEY": "test_secret",
            "ENVIRONMENT": "testing",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from app.config import Settings

            settings = Settings()

            assert settings.heyreach_api_key == "test_heyreach_key"
            assert settings.deepseek_api_key == "test_deepseek_key"
            assert settings.deepseek_model == "deepseek-chat"
            assert settings.telegram_bot_token == "test_telegram_token"
            assert settings.telegram_chat_id == "123456789"
            assert settings.database_url == "postgresql+asyncpg://user:pass@localhost/db"
            assert settings.secret_key == "test_secret"
            assert settings.environment == "testing"

    def test_settings_default_deepseek_model(self):
        """DeepSeek model should default to 'deepseek-chat'."""
        env_vars = {
            "HEYREACH_API_KEY": "test_key",
            "DEEPSEEK_API_KEY": "test_key",
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "123",
            "DATABASE_URL": "postgresql+asyncpg://localhost/db",
            "SECRET_KEY": "secret",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            # Remove DEEPSEEK_MODEL if it exists
            with patch.dict(os.environ, {"DEEPSEEK_MODEL": ""}, clear=False):
                os.environ.pop("DEEPSEEK_MODEL", None)

            from app.config import Settings

            settings = Settings()
            assert settings.deepseek_model == "deepseek-chat"

    def test_settings_default_environment(self):
        """Environment should default to 'development'."""
        env_vars = {
            "HEYREACH_API_KEY": "test_key",
            "DEEPSEEK_API_KEY": "test_key",
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "123",
            "DATABASE_URL": "postgresql+asyncpg://localhost/db",
            "SECRET_KEY": "secret",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            from app.config import Settings

            settings = Settings()
            assert settings.environment == "development"

    def test_is_production_property(self):
        """is_production should return True only in production environment."""
        env_vars = {
            "HEYREACH_API_KEY": "test_key",
            "DEEPSEEK_API_KEY": "test_key",
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "123",
            "DATABASE_URL": "postgresql+asyncpg://localhost/db",
            "SECRET_KEY": "secret",
            "ENVIRONMENT": "production",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from app.config import Settings

            settings = Settings()
            assert settings.is_production is True

        env_vars["ENVIRONMENT"] = "development"
        with patch.dict(os.environ, env_vars, clear=False):
            from app.config import Settings

            settings = Settings()
            assert settings.is_production is False

    def test_deepseek_base_url_property(self):
        """deepseek_base_url should return the DeepSeek API base URL."""
        env_vars = {
            "HEYREACH_API_KEY": "test_key",
            "DEEPSEEK_API_KEY": "test_key",
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "123",
            "DATABASE_URL": "postgresql+asyncpg://localhost/db",
            "SECRET_KEY": "secret",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from app.config import Settings

            settings = Settings()
            assert settings.deepseek_base_url == "https://api.deepseek.com"
