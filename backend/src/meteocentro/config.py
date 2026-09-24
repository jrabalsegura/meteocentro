from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(min_length=20, pattern=r"^postgresql\+psycopg://")
    environment: Literal["development", "test", "production"] = "development"
    private_read: bool = True
    app_origin: str = "http://localhost:5173"
    session_hours: int = Field(default=8760, ge=1, le=8760)
    aemet_enabled: bool = True
    aemet_api_key: SecretStr | None = None
    aemet_poll_seconds: int = Field(default=900, ge=900)
    aemet_daily_http_budget: int = Field(default=400, ge=3)
    aemet_current_reserve: int = Field(default=220, ge=0)
    aemet_minute_http_budget: int = Field(default=20, ge=1, le=40)
    aemet_concurrency: int = Field(default=1, ge=1, le=2)
    meteoclimatic_enabled: bool = False
    # Published licence or specific permission, applicable to the intended use.
    meteoclimatic_terms_reference: str | None = None
    meteoclimatic_poll_seconds: int = Field(default=900, ge=900)
    meteoclimatic_daily_http_budget: int = Field(default=300, ge=1)
    meteoclimatic_current_reserve: int = Field(default=110, ge=0)
    meteoclimatic_minute_http_budget: int = Field(default=20, ge=1, le=20)
    history_daily_http_budget: int = Field(default=20, ge=3, le=100)
    history_coverage_threshold: float = Field(default=0.9, ge=0.5, le=1)
    detail_retention_months: int = Field(default=24, ge=1, le=120)
    worker_lease_seconds: int = Field(default=120, ge=30)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)

    @model_validator(mode="after")
    def quota_reserve(self):
        from urllib.parse import urlsplit

        origin = urlsplit(self.app_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.netloc
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or (self.environment == "production" and origin.scheme != "https")
        ):
            raise ValueError("APP_ORIGIN must be an exact origin; HTTPS required in production")
        self.meteoclimatic_terms_reference = (
            self.meteoclimatic_terms_reference.strip() or None
            if self.meteoclimatic_terms_reference
            else None
        )
        if self.aemet_current_reserve >= self.aemet_daily_http_budget:
            raise ValueError("AEMET_CURRENT_RESERVE must be below AEMET_DAILY_HTTP_BUDGET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
