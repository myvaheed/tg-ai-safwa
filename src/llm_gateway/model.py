"""Neutral values exchanged between an LLM host and a provider."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

type Message = Mapping[str, Any]
type ToolSpec = Mapping[str, Any]
type ResponseSchema = Mapping[str, Any]

# The keys of a chat message every OpenAI-compatible endpoint reads. Whatever a provider
# returns beyond them — a reasoning trace, a signature on a call — is its own.
STANDARD_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call exactly as returned by the provider.

    ``arguments_json`` is deliberately not parsed here. Validation belongs to the
    tool owner, which can report a useful repair message to the model.
    """

    id: str
    name: str
    arguments_json: str
    extensions: Mapping[str, Any] = field(default_factory=dict)
    """What the provider put on the call beyond its id and function, as it returned it:
    a signature on the call rides here."""


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
    extensions: Mapping[str, Any] = field(default_factory=dict)
    """What the provider put on the message beyond `STANDARD_KEYS`, as it returned it:
    its reasoning, in whichever field it uses."""

    def as_message(self) -> dict[str, Any]:
        """This response as the assistant message the next request of its loop carries.

        What the provider added goes back with it, unchanged: a model that thinks between
        its calls continues from its own reasoning, and without it stops thinking, thinks
        into the answer, or is refused the request.
        """
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or (None if self.tool_calls else ""),
            **self.extensions,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                    **call.extensions,
                }
                for call in self.tool_calls
            ]
        return message


def standard_message(message: Message) -> dict[str, Any]:
    """The message as every endpoint reads it, with nothing one provider added.

    What outlives a session is kept in this shape, so a conversation one model wrote reads
    the same to the next: a signature or a reasoning trace goes back only to its author,
    and only while its session runs.
    """
    kept = {key: message[key] for key in STANDARD_KEYS if key in message}
    if "tool_calls" in kept:
        kept["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                },
            }
            for call in kept["tool_calls"]
        ]
    return kept


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Everything a provider needs for one stateless completion."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    tool_choice: str | None = None
    response_schema: ResponseSchema | None = None
    temperature: float | None = 0.2
    reasoning_effort: str | None = None
