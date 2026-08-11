from __future__ import annotations

from safwa.config import Settings
from safwa.constants import LMSTUDIO_BASE_URL, OPENROUTER_BASE_URL
from safwa.enums import AIProvider


def _settings(**overrides) -> Settings:
    values = {"telegram_bot_token": "token", "telegram_owner_id": 1}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_lmstudio_is_the_default_endpoint():
    settings = _settings()

    assert settings.ai_provider is AIProvider.LMSTUDIO
    assert settings.resolved_ai_base_url == LMSTUDIO_BASE_URL
    assert settings.resolved_ai_max_retries == 1
    assert settings.resolved_ai_send_temperature is True
    assert settings.resolved_ai_cache_breakpoints is False


def test_openrouter_derives_its_own_defaults():
    settings = _settings(ai_provider="openrouter", ai_model="openai/gpt-5.6-luna")

    assert settings.resolved_ai_base_url == OPENROUTER_BASE_URL
    assert settings.resolved_ai_max_retries == 3
    # openai/gpt-5.6-luna does not accept temperature.
    assert settings.resolved_ai_send_temperature is False
    assert settings.resolved_ai_cache_breakpoints is True


def test_an_explicit_value_beats_the_provider_default():
    settings = _settings(
        ai_provider="openrouter",
        ai_base_url="http://127.0.0.1:1234/v1",
        ai_max_retries=0,
        ai_send_temperature=True,
        ai_cache_breakpoints=False,
    )

    assert settings.resolved_ai_base_url == "http://127.0.0.1:1234/v1"
    assert settings.resolved_ai_max_retries == 0
    assert settings.resolved_ai_send_temperature is True
    assert settings.resolved_ai_cache_breakpoints is False
