from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .adapters.asr import ASRProvider
from .ai.sql import DEFAULT_CHAR_BUDGET, DEFAULT_ROW_LIMIT
from .constants import (
    AI_MAX_OUTPUT_TOKENS,
    AI_MAX_RETRIES_LOCAL,
    AI_MAX_RETRIES_REMOTE,
    AI_TIMEOUT_SECONDS,
    FASTER_WHISPER_MODEL,
    GROQ_ASR_BASE_URL,
    GROQ_ASR_MODEL,
    LMSTUDIO_BASE_URL,
    LOCAL_ASR_BASE_URL,
    LOCAL_ASR_MODEL,
    MEMORY_POLL_SECONDS,
    MEMORY_TOKEN_BUDGET,
    OPENAI_ASR_BASE_URL,
    OPENAI_ASR_MODEL,
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


@dataclass(frozen=True)
class ASRDefaults:
    base_url: str
    model: str


ASR_DEFAULTS: dict[ASRProvider, ASRDefaults] = {
    ASRProvider.OFF: ASRDefaults(base_url="", model=""),
    ASRProvider.OPENAI: ASRDefaults(base_url=OPENAI_ASR_BASE_URL, model=OPENAI_ASR_MODEL),
    ASRProvider.GROQ: ASRDefaults(base_url=GROQ_ASR_BASE_URL, model=GROQ_ASR_MODEL),
    ASRProvider.LOCAL: ASRDefaults(base_url=LOCAL_ASR_BASE_URL, model=LOCAL_ASR_MODEL),
    # In-process: there is no endpoint to reach, so no base URL either.
    ASRProvider.FASTER_WHISPER: ASRDefaults(base_url="", model=FASTER_WHISPER_MODEL),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAFWA_",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    telegram_bot_username: str = Field(default="", pattern=r"^[A-Za-z0-9_]*$")
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
    ai_tool_choice_required: bool = True
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
    # Voice input. `off` leaves the bot text-only; a voice message then gets one plain reply.
    asr_provider: ASRProvider = ASRProvider.OFF
    asr_api_key: SecretStr = SecretStr("")
    # Left unset these follow ASR_DEFAULTS for the selected asr_provider.
    asr_model: str = ""
    asr_base_url: str = ""
    # An ISO code pins the language; empty leaves the engine to detect one per message.
    asr_language: str = ""
    # faster_whisper only. On every HTTP path the device is the server's problem.
    asr_device: str = Field(default="auto", pattern=r"^(auto|cpu|cuda)$")
    asr_compute_type: str = ""
    asr_log_timing: bool = True
    timezone: str = "Europe/Istanbul"
    summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS
    memory_token_budget: int = MEMORY_TOKEN_BUDGET
    token_chars_estimate: float = TOKEN_CHARS_ESTIMATE
    memory_poll_seconds: float = MEMORY_POLL_SECONDS
    # The Reminder poll. Off means Reminders can be created and scheduled but never fire.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = SCHEDULER_POLL_SECONDS
    log_level: str = Field(default="INFO", pattern=r"^(?i:DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def normalize_bot_username(cls, value: object) -> str:
        return str(value or "").strip().removeprefix("@")

    @field_validator("asr_language", "asr_compute_type", mode="before")
    @classmethod
    def normalize_asr_text(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @field_validator("asr_device", mode="before")
    @classmethod
    def normalize_asr_device(cls, value: object) -> str:
        return str(value or "auto").strip().lower()

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
    def asr_enabled(self) -> bool:
        return self.asr_provider is not ASRProvider.OFF

    @property
    def asr_defaults(self) -> ASRDefaults:
        return ASR_DEFAULTS[self.asr_provider]

    @property
    def resolved_asr_base_url(self) -> str:
        return self.asr_base_url or self.asr_defaults.base_url

    @property
    def resolved_asr_model(self) -> str:
        return self.asr_model or self.asr_defaults.model

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
