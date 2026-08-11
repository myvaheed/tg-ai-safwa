from __future__ import annotations

import asyncio
import getpass
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient

from .config import Settings


class QAConfig(BaseSettings):
    """Opt-in configuration for Safwa-QA and live Telegram integration tests."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAFWA_QA_",
        env_ignore_empty=True,
        extra="ignore",
    )

    telegram_bot_token: SecretStr | None = None
    telegram_owner_id: int | None = None
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_user_session_path: Path = Path("data/qa/telegram-user")
    ai_base_url: str | None = None
    ai_api_key: SecretStr | None = None
    ai_model: str | None = None
    live_timeout_seconds: float = Field(default=60.0, gt=5.0, le=180.0)
    keep_messages: bool = False
    log_level: str = "WARNING"


@dataclass(frozen=True)
class ResolvedQAConfig:
    settings: Settings
    live_timeout_seconds: float
    keep_messages: bool


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    unwrapped = value.get_secret_value().strip()
    return unwrapped or None


def _text(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def resolve_qa_config(
    *,
    data_dir: Path,
    database_url: str,
    base: Settings | None = None,
    qa: QAConfig | None = None,
) -> ResolvedQAConfig:
    """Resolve QA overrides while enforcing separation from production Telegram state."""

    base = base or Settings()
    qa = qa or QAConfig()
    qa_token = _secret(qa.telegram_bot_token)
    if qa_token is None:
        raise ValueError("Set SAFWA_QA_TELEGRAM_BOT_TOKEN for the dedicated Safwa-QA bot")
    production_token = base.telegram_bot_token.get_secret_value()
    if qa_token == production_token:
        raise ValueError("Safwa-QA must not reuse SAFWA_TELEGRAM_BOT_TOKEN")

    qa_session = qa.telegram_user_session_path
    if qa_session.resolve() == base.telegram_user_session_path.resolve():
        raise ValueError("Safwa-QA must use a separate Telegram user session path")

    api_id = qa.telegram_api_id or base.telegram_api_id
    api_hash = _secret(qa.telegram_api_hash) or _secret(base.telegram_api_hash)
    if api_id is None or api_hash is None:
        raise ValueError("Set Telegram API credentials for the Safwa-QA Telethon session")

    owner_id = qa.telegram_owner_id or base.telegram_owner_id
    settings = Settings(
        _env_file=None,
        telegram_bot_token=qa_token,
        telegram_owner_id=owner_id,
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        telegram_history_required=True,
        telegram_user_session_path=qa_session,
        database_url=database_url,
        data_dir=data_dir,
        ai_provider=base.ai_provider,
        ai_base_url=_text(qa.ai_base_url) or base.ai_base_url,
        ai_api_key=_secret(qa.ai_api_key) or base.ai_api_key.get_secret_value(),
        ai_model=_text(qa.ai_model) or base.ai_model,
        ai_timeout_seconds=base.ai_timeout_seconds,
        ai_max_output_tokens=base.ai_max_output_tokens,
        ai_structured_output=base.ai_structured_output,
        ai_max_retries=base.ai_max_retries,
        ai_send_temperature=base.ai_send_temperature,
        ai_cache_breakpoints=base.ai_cache_breakpoints,
        ai_reasoning_effort=base.ai_reasoning_effort,
        timezone=base.timezone,
        summary_trigger_tokens=base.summary_trigger_tokens,
        memory_token_budget=base.memory_token_budget,
        token_chars_estimate=base.token_chars_estimate,
        memory_poll_seconds=base.memory_poll_seconds,
        scheduler_enabled=base.scheduler_enabled,
        scheduler_poll_seconds=base.scheduler_poll_seconds,
        log_level=qa.log_level,
    )
    return ResolvedQAConfig(settings, qa.live_timeout_seconds, qa.keep_messages)


def auth_main() -> None:
    """Authorize the separate Telegram user session used by live QA tests."""

    resolved = resolve_qa_config(
        data_dir=Path("data/qa"),
        database_url="sqlite:///data/qa/safwa.db",
    )
    settings = resolved.settings

    async def authenticate() -> None:
        settings.telegram_user_session_path.parent.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(
            str(settings.telegram_user_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),  # type: ignore[union-attr]
        )
        await client.start(
            phone=lambda: input("Telegram phone: "),
            code_callback=lambda: input("Telegram code: "),
            password=lambda: getpass.getpass("2FA password: "),
        )
        user = await client.get_me()
        if user.id != settings.telegram_owner_id:
            await client.disconnect()
            raise RuntimeError(
                "The authorized QA Telegram user does not match SAFWA_QA_TELEGRAM_OWNER_ID"
            )
        await client.disconnect()

    asyncio.run(authenticate())


if __name__ == "__main__":
    auth_main()
