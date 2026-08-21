"""Provider-neutral boundary for one LLM completion."""

from .model import CompletionRequest, CompletionTurn, ToolCall, Usage
from .openai_compatible import OpenAICompatibleConfig, OpenAICompatibleProvider
from .provider import LlmProvider
from .testing import ScriptedProvider

__all__ = [
    "CompletionRequest",
    "CompletionTurn",
    "LlmProvider",
    "OpenAICompatibleConfig",
    "OpenAICompatibleProvider",
    "ScriptedProvider",
    "ToolCall",
    "Usage",
]
