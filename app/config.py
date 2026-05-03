from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_string_bryan: str
    telegram_session_string_bryan2: str
    telegram_group_id: int
    group_dataflow: int = -1003340385645
    group_tamaki: int = -1002411246251
    telegram_timeout: int = 15
    rate_limit_interval: float = 3.0
    max_requests_per_minute: int = 20
    cache_ttl_hours: int = 24
    voidai_api_key: str = ""
    api_secret_key: str = ""
    api_keys: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "*"

    @property
    def allowed_api_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        if self.api_secret_key.strip():
            keys.append(self.api_secret_key.strip())
        for key in self.api_keys.split(","):
            normalized = key.strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
        return tuple(keys)

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        return origins or ["*"]

    @property
    def primary_api_key(self) -> str | None:
        if not self.allowed_api_keys:
            return None
        return self.allowed_api_keys[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()