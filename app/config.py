"""Configuration management using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # HeyReach
    heyreach_api_key: str = ""

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # Slack
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    slack_metrics_channel_id: str = ""  # For daily/weekly reports (falls back to slack_channel_id)
    slack_engagement_channel_id: str = ""  # For LinkedIn engagement notifications
    slack_pitched_channel_id: str = ""  # For pitched+ prospect tracking cards
    slack_signing_secret: str = ""

    # Apify
    apify_api_token: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./speed_to_lead.db"
    content_db_url: str = ""  # contentCreator's Postgres (for trend scout)

    # Perplexity (for trend scout)
    perplexity_api_key: str = ""

    # Anthropic (for trend scout ICP scoring)
    anthropic_api_key: str = ""

    # Google Sheets (for gift leads)
    google_service_account_json: str = ""
    google_drive_folder_id: str = ""  # Shared folder owned by personal account
    google_oauth_client_id: str = ""  # OAuth client ID (personal account)
    google_oauth_client_secret: str = ""  # OAuth client secret
    google_oauth_refresh_token: str = ""  # OAuth refresh token (personal account)

    # App
    secret_key: str = "change-me-in-production"
    environment: str = "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    @property
    def deepseek_base_url(self) -> str:
        """Get the DeepSeek API base URL."""
        return "https://api.deepseek.com"

    @property
    def async_database_url(self) -> str:
        """Get database URL for async driver (asyncpg)."""
        url = self.database_url
        # Convert postgresql:// to postgresql+asyncpg://
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience instance - import this in other modules
settings = get_settings()
