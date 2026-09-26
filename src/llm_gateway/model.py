"""Neutral values exchanged between an LLM host and a provider."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

type Message = Mapping[str, Any]
type ToolSpec = Mapping[str, Any]
type ResponseSchema = Mapping[str, Any]

# Where a provider returns the reasoning behind a response: LM Studio `reasoning_content`,
# OpenRouter `reasoning` and `reasoning_details`. OpenRouter asks for them back unchanged.
REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_details")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call exactly as returned by the provider.

    ``arguments_json`` is deliberately not parsed here. Validation belongs to the
    tool owner, which can report a useful repair message to the model.
    """

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float | None = None


@dataclass(frozen=True, slots=True)
class CompletionTurn:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    reasoning: Mapping[str, Any] = field(default_factory=dict)
    """The provider's `REASONING_FIELDS` exactly as it returned them."""

    def as_message(self) -> dict[str, Any]:
        """This response as the assistant message the next request of its loop carries.

        The reasoning goes back with it: a model that thinks between its calls continues
        from its own reasoning, and without it stops thinking or thinks into the answer.
        """
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or (None if self.tool_calls else ""),
            **self.reasoning,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in self.tool_calls
            ]
        return message


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Everything a provider needs for one stateless completion."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    tool_choice: str | None = None
    response_schema: ResponseSchema | None = None
    temperature: float | None = 0.2
    reasoning_effort: str | None = None
