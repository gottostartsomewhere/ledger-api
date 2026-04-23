from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Ledger API"
    app_env: str = Field(default="development")
    debug: bool = Field(default=False)

    database_url: str = Field(...)
    redis_url: str = Field(...)

    jwt_secret: str = Field(...)
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_ttl_minutes: int = Field(default=60)
    jwt_refresh_ttl_days: int = Field(default=30)

    rate_limit_per_minute: int = Field(default=60)
    rate_limit_auth_per_minute: int = Field(default=10)

    system_account_currency: str = Field(default="USD")

    cors_origins: str = Field(default="*")
    log_level: str = Field(default="INFO")

    webhook_max_attempts: int = Field(default=8)
    webhook_timeout_seconds: float = Field(default=5.0)

    admin_emails: str = Field(default="")

    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    def admin_emails_set(self) -> set[str]:
        raw = (self.admin_emails or "").strip()
        if not raw:
            return set()
        return {e.strip().lower() for e in raw.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
