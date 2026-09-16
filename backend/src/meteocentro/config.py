from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=20, pattern=r"^postgresql\+psycopg://")
    environment: str = "development"
    aemet_api_key: SecretStr | None = None
    aemet_poll_seconds: int = Field(default=900, ge=900)
    aemet_daily_http_budget: int = Field(default=400, ge=3)
    aemet_current_reserve: int = Field(default=220, ge=0)
    aemet_minute_http_budget: int = Field(default=20, ge=1, le=40)
    aemet_concurrency: int = Field(default=1, ge=1, le=2)
    worker_lease_seconds: int = Field(default=120, ge=30)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)

    @model_validator(mode="after")
    def quota_reserve(self):
        if self.aemet_current_reserve >= self.aemet_daily_http_budget:
            raise ValueError("AEMET_CURRENT_RESERVE must be below AEMET_DAILY_HTTP_BUDGET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
