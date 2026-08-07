from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAFWA_",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    telegram_owner_id: int
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_history_required: bool = True
    telegram_user_session_path: Path = Path("data/telegram-user")
    database_url: str = "sqlite:///data/safwa.db"
    data_dir: Path = Path("data")
    ai_base_url: str = "http://localhost:1234/v1"
    ai_api_key: SecretStr = SecretStr("lm-studio")
    ai_model: str = "local-model"
    ai_timeout_seconds: float = 120.0
    ai_max_output_tokens: int = 4096
    ai_structured_output: bool = False
    timezone: str = "Europe/Istanbul"
    summary_trigger_tokens: int = 10_000
    memory_token_budget: int = 4_000
    token_chars_estimate: float = 3.0
    memory_poll_seconds: float = 5.0
    scheduler_poll_seconds: float = 30.0
    log_level: str = Field(default="INFO", pattern=r"^(?i:DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("sqlite+aiosqlite:"):
            return self.database_url
        return self.database_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    @property
    def memory_path(self) -> Path:
        return self.data_dir / "memory.md"

    @property
    def telegram_history_enabled(self) -> bool:
        return self.telegram_api_id is not None and self.telegram_api_hash is not None
