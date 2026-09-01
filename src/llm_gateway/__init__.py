"""Provider-neutral boundary for one LLM completion."""

from .model import CompletionRequest, CompletionTurn, ToolCall, Usage
from .openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleError,
    OpenAICompatibleProvider,
    create_openai_client,
)
from .provider import LlmProvider
from .testing import ScriptedProvider

__all__ = [
    "CompletionRequest",
    "CompletionTurn",
    "LlmProvider",
    "OpenAICompatibleConfig",
    "OpenAICompatibleError",
    "OpenAICompatibleProvider",
    "ScriptedProvider",
    "ToolCall",
    "Usage",
    "create_openai_client",
]
