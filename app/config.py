from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_string_bryan: str
    telegram_session_string_bryan2: str
    telegram_group_id: int
    telegram_timeout: int = 15
    rate_limit_interval: float = 3.0
    max_requests_per_minute: int = 20
    cache_ttl_hours: int = 24
    api_secret_key: str


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()