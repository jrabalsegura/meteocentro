from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=20, pattern=r"^postgresql\+psycopg://")
    environment: str = "development"
    aemet_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
