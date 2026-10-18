from __future__ import annotations

from pathlib import Path

import pytest

from safwa.config import Settings
from safwa.qa import QAConfig, qa_provider_settings, resolve_qa_config


def production_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="100:production",
        telegram_owner_id=42,
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
        telegram_api_id=12345,
        telegram_api_hash="api-hash",
        ai_base_url=None,
        ai_api_key=None,
        ai_model=None,
    )
    resolved = resolve_qa_config(
        data_dir=tmp_path / "runtime",
        database_path=tmp_path / "qa.db",
        base=base,
        qa=qa,
    )

    settings = resolved.settings
    assert settings.telegram_bot_token.get_secret_value() == "200:qa"
    assert settings.telegram_bot_username == "safwa_qa_bot"
    assert settings.telegram_owner_id == base.telegram_owner_id
    assert settings.ai_model == base.ai_model
    assert settings.data_dir == tmp_path / "runtime"
    assert resolved.keep_messages is False
    # The owner's side of a live test signs in; Safwa itself never does.
    assert (
        resolved.telegram_api_id,
        resolved.telegram_api_hash,
        resolved.telegram_user_session_path,
    ) == (12345, "api-hash", tmp_path / "qa-user")


def test_qa_config_rejects_the_production_bot(tmp_path: Path):
    base = production_settings(tmp_path)
    qa = QAConfig(_env_file=None, telegram_bot_token="100:production")

    with pytest.raises(ValueError, match="must not reuse"):
        resolve_qa_config(
            data_dir=tmp_path / "runtime",
            database_path=tmp_path / "qa.db",
            base=base,
            qa=qa,
        )


def test_a_live_provider_check_talks_to_the_qa_provider(tmp_path: Path):
    base = production_settings(tmp_path)
    qa = QAConfig(_env_file=None, ai_base_url="http://localhost:1234/v1", ai_model="qa-model")
    config = qa_provider_settings(base, qa).ai_config()
    assert (config.base_url, config.model) == ("http://localhost:1234/v1", "qa-model")
    # What QA does not set comes from the provider Safwa runs on.
    assert config.api_key == "provider-key"
    assert qa_provider_settings(base, QAConfig(_env_file=None)).ai_config().model == "provider-model"
