"""Neutral values exchanged between an LLM host and a provider."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

type Message = Mapping[str, Any]
type ToolSpec = Mapping[str, Any]
type ResponseSchema = Mapping[str, Any]


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


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Everything a provider needs for one stateless completion."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    tool_choice: str | None = None
    response_schema: ResponseSchema | None = None
    temperature: float | None = 0.2
    reasoning_effort: str | None = None
