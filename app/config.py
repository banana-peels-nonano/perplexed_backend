"""Application settings, read once from the environment and the .env file.

Every other module asks get_settings() for configuration instead of reading
os.environ itself, so there is exactly one place that knows where settings come from.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads .env from the current working directory (run commands from the repo root).
    # Real environment variables win over .env values. Unknown keys are ignored, so
    # POSTGRES_PASSWORD (which only docker compose needs) doesn't cause an error.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Field names match env vars case-insensitively: DATABASE_URL -> database_url.
    database_url: str
    test_database_url: str | None = None

    # SecretStr hides the value in repr/logs: print(settings) shows '**********'.
    # Read the real value only where it is used: settings.anthropic_api_key.get_secret_value()
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    # Connection pool. D-005: never hold a connection across an LLM call, so 10 + 10
    # connections serve many more concurrent interviews than 20.
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # Session cookie. Secure=True means the browser only sends it over HTTPS:
    # keep False for local http://localhost, set COOKIE_SECURE=true behind Caddy/TLS.
    cookie_secure: bool = False
    session_ttl_hours: int = 24  # an overnight event fits in one session

    # Organiser API (/api/v1/admin/*): requests must send "Authorization: Bearer <token>".
    # Unset = the organiser API doesn't exist (404). At least 32 characters.
    admin_token: SecretStr | None = None


    @field_validator("database_url", "test_database_url")
    @classmethod
    def _async_driver(cls, v: str | None) -> str | None:
        """Hosts (Render, Heroku) hand out postgres:// URLs; SQLAlchemy needs the driver."""
        if v and v.startswith(("postgres://", "postgresql://")):
            return "postgresql+asyncpg://" + v.split("://", 1)[1]
        return v

    @field_validator("admin_token")
    @classmethod
    def _long_enough(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and v.get_secret_value() and len(v.get_secret_value()) < 32:
            raise ValueError("ADMIN_TOKEN must be at least 32 characters")
        return v if (v is None or v.get_secret_value()) else None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process; later calls return the same object."""
    return Settings()
