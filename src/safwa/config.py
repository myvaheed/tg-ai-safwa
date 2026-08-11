from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    AI_MAX_OUTPUT_TOKENS,
    AI_TIMEOUT_SECONDS,
    DEFAULT_CHAR_BUDGET,
    DEFAULT_ROW_LIMIT,
    MEMORY_POLL_SECONDS,
    MEMORY_TOKEN_BUDGET,
    SCHEDULER_POLL_SECONDS,
    SUMMARY_TRIGGER_TOKENS,
    TOKEN_CHARS_ESTIMATE,
)


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
    ai_timeout_seconds: float = AI_TIMEOUT_SECONDS
    ai_max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    ai_structured_output: bool = False
    # query_safwa result caps. A capped result is returned with a notice telling the
    # model to narrow the query, so raise these only if the model has context to spare.
    ai_query_row_limit: int = Field(default=DEFAULT_ROW_LIMIT, gt=0)
    ai_query_char_budget: int = Field(default=DEFAULT_CHAR_BUDGET, gt=0)
    timezone: str = "Europe/Istanbul"
    summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS
    memory_token_budget: int = MEMORY_TOKEN_BUDGET
    token_chars_estimate: float = TOKEN_CHARS_ESTIMATE
    memory_poll_seconds: float = MEMORY_POLL_SECONDS
    scheduler_poll_seconds: float = SCHEDULER_POLL_SECONDS
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
