"""Provider-neutral boundary for one LLM completion."""

from .model import (
    STANDARD_KEYS,
    CompletionRequest,
    CompletionTurn,
    ToolCall,
    Usage,
    standard_message,
)
from .openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleError,
    OpenAICompatibleProvider,
    create_openai_client,
)
from .presets import DEFAULT_PRESET, PRESETS, preset_config
from .provider import LlmProvider
from .pseudonymize import PiiFinder, PseudonymizingProvider, Span
from .testing import ScriptedProvider

__all__ = [
    "DEFAULT_PRESET",
    "PRESETS",
    "STANDARD_KEYS",
    "CompletionRequest",
    "CompletionTurn",
    "LlmProvider",
    "OpenAICompatibleConfig",
    "OpenAICompatibleError",
    "OpenAICompatibleProvider",
    "PiiFinder",
    "PseudonymizingProvider",
    "ScriptedProvider",
    "Span",
    "ToolCall",
    "Usage",
    "create_openai_client",
    "preset_config",
    "standard_message",
]
