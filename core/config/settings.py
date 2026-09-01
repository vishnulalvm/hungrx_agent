from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration, sourced from environment variables / .env.

    Shared by every Python service in the monorepo (api, worker) so they
    agree on connection strings and secrets without duplicating parsing
    logic.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General ---
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "info"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/hungrx"

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = "change-me"
    cors_origins: str = "http://localhost:3000"

    # --- Auth ---
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_minutes: int = 60 * 24 * 14

    # --- Admin dashboard ---
    next_public_api_base_url: str = "http://localhost:8000"

    # --- LangGraph / LLM ---
    anthropic_api_key: str = ""
    langgraph_checkpoint_url: str = ""

    # --- AI provider (Multimodal Translation / future AI nodes) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-2024-08-06"  # first model generation with strict json_schema mode

    # --- Crawler ---
    playwright_headless: bool = True
    crawler_user_agent: str = "hungrx-crawler/1.0"

    # --- Storage ---
    storage_backend: str = "local"
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
