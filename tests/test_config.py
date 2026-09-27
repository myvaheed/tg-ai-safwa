from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_gateway import PRESETS
from safwa.config import Settings


def _settings(**overrides) -> Settings:
    values = {"telegram_bot_token": "token", "telegram_owner_id": 1}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_a_local_server_is_the_default_endpoint():
    config = _settings().ai_config()

    assert _settings().ai_provider == "local"
    assert config.base_url == PRESETS["local"]["base_url"]
    assert config.max_retries == 1
    assert config.send_temperature is True
    assert config.cache_breakpoints is False


def test_bot_username_is_normalized_for_deep_links():
    settings = _settings(telegram_bot_username=" @safwa_ai_bot ")

    assert settings.telegram_bot_username == "safwa_ai_bot"


def test_openrouter_derives_its_own_defaults():
    config = _settings(ai_provider="OpenRouter", ai_model="openai/gpt-5.6-luna").ai_config()

    assert config.base_url == PRESETS["openrouter"]["base_url"]
    assert config.max_retries == 3
    # openai/gpt-5.6-luna does not accept temperature.
    assert config.send_temperature is False
    assert config.cache_breakpoints is True
    assert config.model == "openai/gpt-5.6-luna"


def test_an_explicit_value_beats_the_provider_default():
    config = _settings(
        ai_provider="openrouter",
        ai_base_url="http://127.0.0.1:1234/v1",
        ai_max_retries=0,
        ai_send_temperature=True,
        ai_cache_breakpoints=False,
    ).ai_config()

    assert config.base_url == "http://127.0.0.1:1234/v1"
    assert config.max_retries == 0
    assert config.send_temperature is True
    assert config.cache_breakpoints is False


def test_an_unknown_provider_is_refused_with_the_known_ones():
    with pytest.raises(ValidationError, match="local, openrouter"):
        _settings(ai_provider="acme")
