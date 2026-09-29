"""Service settings, loaded from environment variables (and ``.env`` if present)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mtlj:mtlj@localhost:5432/mtlj"
    cors_origins: list[str] = ["http://localhost:3000"]
    enable_testing_routes: bool = True
    external_check_url: str = "https://api.github.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
