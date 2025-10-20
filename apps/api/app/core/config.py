from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str | None = None
    CELERY_BACKEND_URL: str | None = None
    INTERNAL_API_KEY: str | None = None
    USE_CELERY: bool = True

    # Pydantic v2 settings config: load .env and ignore unknown keys
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
