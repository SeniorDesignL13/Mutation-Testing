"""API settings, read from environment variables (and ``.env`` if present)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting has a default that works on your machine; Docker overrides some."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mtlj:mtlj@localhost:5432/mtlj"
    # Websites allowed to call the API from a browser.
    cors_origins: list[str] = ["http://localhost:3000"]
    # The /testing diagnostic routes behind the status page.
    enable_testing_routes: bool = True
    external_check_url: str = "https://api.github.com"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once and then reused."""
    return Settings()
