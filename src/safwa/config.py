from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from llm_gateway import DEFAULT_PRESET, PRESETS, OpenAICompatibleConfig, preset_config
from tg_agent_shell.ai.sql import DEFAULT_CHAR_BUDGET, DEFAULT_ROW_LIMIT
from tg_agent_shell.asr import ASR_DEFAULTS, ASRDefaults, ASRProvider

from .constants import SCHEDULER_POLL_SECONDS, SUMMARY_TRIGGER_TOKENS
from .foundation.tokens import TOKEN_CHARS_ESTIMATE

AI_TIMEOUT_SECONDS = 120.0
AI_MAX_OUTPUT_TOKENS = 4096
# Request attribution a gateway may show beside the requests; an endpoint that does not
# read these headers ignores them.
AI_ATTRIBUTION_HEADERS = (
    ("HTTP-Referer", "https://github.com/myvaheed/tg-ai-safwa"),
    ("X-Title", "Safwa"),
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAFWA_",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    telegram_bot_username: str = Field(default="", pattern=r"^[A-Za-z0-9_]*$")
    telegram_owner_id: int
    database_path: Path = Path("data/safwa.db")
    data_dir: Path = Path("data")
    # A row of llm_gateway.PRESETS: local or openrouter.
    ai_provider: str = DEFAULT_PRESET
    ai_api_key: SecretStr = SecretStr("lm-studio")
    ai_model: str = "local-model"
    ai_timeout_seconds: float = AI_TIMEOUT_SECONDS
    ai_max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    ai_structured_output: bool = False
    ai_tool_choice_required: bool = True
    # Left unset these follow the ai_provider's row.
    ai_base_url: str | None = None
    ai_max_retries: int | None = Field(default=None, ge=0)
    ai_send_temperature: bool | None = None
    ai_cache_breakpoints: bool | None = None
    ai_reasoning_effort: str | None = None
    # query_data result caps. A capped result is returned with a notice telling the
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
    # Photos. Off, a photo gets one plain reply. On, the AI model must read images: it
    # labels each photo as it arrives and looks at one again when asked.
    image_input: bool = False
    timezone: str = "Europe/Istanbul"
    summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS
    token_chars_estimate: float = TOKEN_CHARS_ESTIMATE
    # The Reminder poll. Off means Reminders can be created and scheduled but never fire.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = SCHEDULER_POLL_SECONDS
    log_level: str = Field(default="INFO", pattern=r"^(?i:DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def normalize_bot_username(cls, value: object) -> str:
        return str(value or "").strip().removeprefix("@")

    @field_validator("ai_provider", mode="before")
    @classmethod
    def known_ai_provider(cls, value: object) -> str:
        name = str(value or "").strip().lower()
        if name not in PRESETS:
            raise ValueError(f"ai_provider must be one of: {', '.join(PRESETS)}")
        return name

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

    def ai_config(self) -> OpenAICompatibleConfig:
        """The ai_provider's row, with every SAFWA_AI_* value that is set put over it."""
        return preset_config(
            self.ai_provider,
            base_url=self.ai_base_url,
            api_key=self.ai_api_key.get_secret_value(),
            model=self.ai_model,
            timeout_seconds=self.ai_timeout_seconds,
            max_output_tokens=self.ai_max_output_tokens,
            structured_output=self.ai_structured_output,
            tool_choice_required=self.ai_tool_choice_required,
            max_retries=self.ai_max_retries,
            send_temperature=self.ai_send_temperature,
            cache_breakpoints=self.ai_cache_breakpoints,
            reasoning_effort=self.ai_reasoning_effort,
            default_headers=AI_ATTRIBUTION_HEADERS,
        )

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
