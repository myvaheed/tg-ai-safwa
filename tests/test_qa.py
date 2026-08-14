from __future__ import annotations

from pathlib import Path

import pytest

from safwa.config import Settings
from safwa.qa import QAConfig, resolve_qa_config


def production_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="100:production",
        telegram_owner_id=42,
        telegram_api_id=12345,
        telegram_api_hash="api-hash",
        telegram_user_session_path=tmp_path / "production-user",
        ai_base_url="http://127.0.0.1:1234/v1",
        ai_api_key="provider-key",
        ai_model="provider-model",
    )


def test_qa_config_inherits_safe_non_bot_defaults(tmp_path: Path):
    base = production_settings(tmp_path)
    qa = QAConfig(
        _env_file=None,
        telegram_bot_token="200:qa",
        telegram_bot_username="safwa_qa_bot",
        telegram_user_session_path=tmp_path / "qa-user",
        telegram_owner_id=None,
        telegram_api_id=None,
        telegram_api_hash=None,
        ai_base_url=None,
        ai_api_key=None,
        ai_model=None,
    )
    resolved = resolve_qa_config(
        data_dir=tmp_path / "runtime",
        database_url=f"sqlite:///{(tmp_path / 'qa.db').as_posix()}",
        base=base,
        qa=qa,
    )

    settings = resolved.settings
    assert settings.telegram_bot_token.get_secret_value() == "200:qa"
    assert settings.telegram_bot_username == "safwa_qa_bot"
    assert settings.telegram_owner_id == base.telegram_owner_id
    assert settings.telegram_api_id == base.telegram_api_id
    assert settings.telegram_api_hash.get_secret_value() == "api-hash"
    assert settings.ai_model == base.ai_model
    assert settings.data_dir == tmp_path / "runtime"
    assert settings.telegram_history_required is True
    assert resolved.keep_messages is False


@pytest.mark.parametrize("reuse", ["token", "session"])
def test_qa_config_rejects_production_telegram_state(tmp_path: Path, reuse: str):
    base = production_settings(tmp_path)
    qa = QAConfig(
        _env_file=None,
        telegram_bot_token="100:production" if reuse == "token" else "200:qa",
        telegram_user_session_path=(
            base.telegram_user_session_path if reuse == "session" else tmp_path / "qa-user"
        ),
    )

    with pytest.raises(ValueError, match="must not reuse|separate Telegram user session"):
        resolve_qa_config(
            data_dir=tmp_path / "runtime",
            database_url=f"sqlite:///{(tmp_path / 'qa.db').as_posix()}",
            base=base,
            qa=qa,
        )
