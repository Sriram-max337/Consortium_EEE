from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_FRONTEND_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class Settings(BaseSettings):
    """Application settings loaded from the environment or a local .env file."""

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    app_env: str = "development"
    app_build_id: str = "unknown"
    frontend_origins: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def default_empty_app_env(cls, value: str) -> str:
        return value.strip() if value and value.strip() else "development"

    @property
    def allowed_frontend_origins(self) -> list[str]:
        configured_origins = [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]
        if configured_origins:
            return configured_origins
        if self.app_env.lower() == "development":
            return list(DEVELOPMENT_FRONTEND_ORIGINS)
        return []


@lru_cache

def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
