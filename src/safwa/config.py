from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    AI_MAX_OUTPUT_TOKENS,
    AI_MAX_RETRIES_LOCAL,
    AI_MAX_RETRIES_REMOTE,
    AI_TIMEOUT_SECONDS,
    DEFAULT_CHAR_BUDGET,
    DEFAULT_ROW_LIMIT,
    LMSTUDIO_BASE_URL,
    MEMORY_POLL_SECONDS,
    MEMORY_TOKEN_BUDGET,
    OPENROUTER_BASE_URL,
    SCHEDULER_POLL_SECONDS,
    SUMMARY_TRIGGER_TOKENS,
    TOKEN_CHARS_ESTIMATE,
)
from .enums import AIProvider


@dataclass(frozen=True)
class ProviderDefaults:
    """Per-endpoint defaults for knobs the owner rarely needs to set by hand."""

    base_url: str
    max_retries: int
    # GPT-5.6 and other reasoning models reject `temperature`; a local model needs it.
    send_temperature: bool
    # Content-block `cache_control` markers. A local server may reject the extra key.
    cache_breakpoints: bool


PROVIDER_DEFAULTS: dict[AIProvider, ProviderDefaults] = {
    AIProvider.LMSTUDIO: ProviderDefaults(
        base_url=LMSTUDIO_BASE_URL,
        max_retries=AI_MAX_RETRIES_LOCAL,
        send_temperature=True,
        cache_breakpoints=False,
    ),
    AIProvider.OPENROUTER: ProviderDefaults(
        base_url=OPENROUTER_BASE_URL,
        max_retries=AI_MAX_RETRIES_REMOTE,
        send_temperature=False,
        cache_breakpoints=True,
    ),
}


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
    ai_provider: AIProvider = AIProvider.LMSTUDIO
    ai_api_key: SecretStr = SecretStr("lm-studio")
    ai_model: str = "local-model"
    ai_timeout_seconds: float = AI_TIMEOUT_SECONDS
    ai_max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    ai_structured_output: bool = False
    # Left unset these follow PROVIDER_DEFAULTS for the selected ai_provider.
    ai_base_url: str | None = None
    ai_max_retries: int | None = Field(default=None, ge=0)
    ai_send_temperature: bool | None = None
    ai_cache_breakpoints: bool | None = None
    ai_reasoning_effort: str | None = None
    # query_safwa result caps. A capped result is returned with a notice telling the
    # model to narrow the query, so raise these only if the model has context to spare.
    ai_query_row_limit: int = Field(default=DEFAULT_ROW_LIMIT, gt=0)
    ai_query_char_budget: int = Field(default=DEFAULT_CHAR_BUDGET, gt=0)
    timezone: str = "Europe/Istanbul"
    summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS
    memory_token_budget: int = MEMORY_TOKEN_BUDGET
    token_chars_estimate: float = TOKEN_CHARS_ESTIMATE
    memory_poll_seconds: float = MEMORY_POLL_SECONDS
    # The Reminder poll. Off means Reminders can be created and scheduled but never fire.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = SCHEDULER_POLL_SECONDS
    log_level: str = Field(default="INFO", pattern=r"^(?i:DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def provider_defaults(self) -> ProviderDefaults:
        return PROVIDER_DEFAULTS[self.ai_provider]

    @property
    def resolved_ai_base_url(self) -> str:
        return self.ai_base_url or self.provider_defaults.base_url

    @property
    def resolved_ai_max_retries(self) -> int:
        if self.ai_max_retries is None:
            return self.provider_defaults.max_retries
        return self.ai_max_retries

    @property
    def resolved_ai_send_temperature(self) -> bool:
        if self.ai_send_temperature is None:
            return self.provider_defaults.send_temperature
        return self.ai_send_temperature

    @property
    def resolved_ai_cache_breakpoints(self) -> bool:
        if self.ai_cache_breakpoints is None:
            return self.provider_defaults.cache_breakpoints
        return self.ai_cache_breakpoints

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
