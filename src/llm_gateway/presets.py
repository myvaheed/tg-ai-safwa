"""What each endpoint needs that the next one does not, one row per endpoint.

A row names only what differs from `OpenAICompatibleConfig`'s defaults, and every value is
a field of it, so adding an endpoint or a gateway is a row here and nothing anywhere else.
Which models behind each row are known to work, and what breaks on which, is
docs/LLM_GATEWAY.md.
"""

from __future__ import annotations

from typing import Any

from .openai_compatible import OpenAICompatibleConfig

# A local server either answers or is down; a metered remote returns 429/502 and is worth
# retrying with the SDK's backoff.
_REMOTE_RETRIES = 3

PRESETS: dict[str, dict[str, Any]] = {
    # Any server on this machine that speaks the standard: LM Studio's address by default,
    # Ollama, llama.cpp's llama-server or vLLM with SAFWA_AI_BASE_URL.
    "local": {
        "base_url": "http://localhost:1234/v1",
        "max_retries": 1,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "max_retries": _REMOTE_RETRIES,
        # GPT-5 and the other reasoning models refuse a temperature.
        "send_temperature": False,
        "cache_breakpoints": True,
    },
}

DEFAULT_PRESET = "local"


def preset_config(preset: str, **values: Any) -> OpenAICompatibleConfig:
    """The endpoint's row, with every value given that is not None put over it."""
    return OpenAICompatibleConfig(
        **{**PRESETS[preset], **{name: value for name, value in values.items() if value is not None}}
    )
