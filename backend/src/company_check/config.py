from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # SQLAlchemy URL (psycopg 3 driver). The same database also holds LangGraph checkpoints.
    database_url: str = "postgresql+psycopg://app:app@localhost:5433/company_check"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "company-check"

    llm_model: str = "gemini-3.5-flash-lite"
    # Used when the primary model is overloaded (503) or rate limited (429).
    llm_fallback_model: str | None = "gemini-3.5-flash"
    # Requests per minute we allow ourselves, a little under the provider's limit (free tier:
    # 15 for Flash-Lite, 5 for Flash). Calls over it wait instead of failing. 0 = no limit.
    llm_rpm: int = 14
    llm_fallback_rpm: int = 4
    llm_provider: str = "google_genai"
    google_api_key: str | None = None

    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def psycopg_dsn(self) -> str:
        """Plain libpq DSN for raw psycopg connections (LISTEN/NOTIFY, LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
