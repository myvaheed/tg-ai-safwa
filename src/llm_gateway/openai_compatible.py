"""Adapter for the OpenAI-compatible chat-completions API.

Every difference between two endpoints is a field of `OpenAICompatibleConfig`, and the
values each endpoint needs are one row of `presets.PRESETS`. Nothing here asks which
provider it is talking to.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from .model import CompletionRequest, CompletionTurn, Message, ToolCall, Usage

logger = logging.getLogger(__name__)


OpenAICompatibleError = OpenAIError

# A call id every endpoint takes back: Anthropic allows only these characters, OpenAI at
# most 40 of them. The id is kept with the conversation and read by the next model too.
_PORTABLE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
# A call's position in a streamed list. It carries nothing the order does not.
_NOT_AN_EXTENSION = frozenset({"index"})
_CACHE_MARKER = {"type": "ephemeral"}


def create_openai_client(
    *,
    base_url: str,
    api_key: str,
    max_retries: int,
    timeout_seconds: float | None = None,
    default_headers: Mapping[str, str] | None = None,
) -> AsyncOpenAI:
    """Create the SDK client shared by OpenAI-compatible adapters."""

    options: dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "max_retries": max_retries,
    }
    if timeout_seconds is not None:
        options["timeout"] = timeout_seconds
    if default_headers:
        options["default_headers"] = dict(default_headers)
    return AsyncOpenAI(**options)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 120.0
    max_output_tokens: int = 4096
    structured_output: bool = False
    max_retries: int = 1
    send_temperature: bool = True
    tool_choice_required: bool = True
    reasoning_effort: str | None = None
    default_headers: tuple[tuple[str, str], ...] = ()
    empty_response_attempts: int = 2
    # Anthropic caches only up to a marked block; every other provider caches a prefix itself.
    cache_breakpoints: bool = False


class OpenAICompatibleProvider:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config
        self.client = create_openai_client(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            default_headers=dict(config.default_headers) or None,
        )

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        config = self.config
        options: dict[str, Any] = {
            "model": config.model,
            "messages": _with_cache_breakpoints(request.messages)
            if config.cache_breakpoints
            else list(request.messages),
            "max_tokens": config.max_output_tokens,
            "stream": False,
        }
        if config.send_temperature and request.temperature is not None:
            options["temperature"] = request.temperature
        reasoning_effort = request.reasoning_effort or config.reasoning_effort
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        if request.response_schema and config.structured_output:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "completion_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if request.tools:
            options["tools"] = list(request.tools)
            options["tool_choice"] = (
                request.tool_choice
                if request.tool_choice and config.tool_choice_required
                else "auto"
            )

        detail = ""
        for attempt in range(1, config.empty_response_attempts + 1):
            response = await self.client.chat.completions.create(**options)
            turn, detail = _read_turn(response)
            if turn is not None:
                return turn
            if attempt < config.empty_response_attempts:
                logger.warning("LLM provider returned an empty response (%s); retrying", detail)
        raise RuntimeError(f"LLM provider returned an empty response: {detail}")

    async def aclose(self) -> None:
        await self.client.close()


def _with_cache_breakpoints(messages: Sequence[Message]) -> list[Message]:
    """Mark the prompt, and the last message before the newest request, as reusable.

    The request that follows is new each turn and the steps of a turn grow after it, so what
    ends before it is the longest prefix the next request repeats byte for byte.
    """
    marked = list(messages)
    requests = [index for index, message in enumerate(marked) if message.get("role") == "user"]
    for index in {0, requests[-1] - 1 if requests else 0}:
        if index >= 0:
            marked[index] = _cache_breakpoint(marked[index])
    return marked


def _cache_breakpoint(message: Message) -> Message:
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message
    return {
        **message,
        "content": [{"type": "text", "text": content, "cache_control": _CACHE_MARKER}],
    }


def _extra(payload: Any, field: str) -> Any:
    if payload is None:
        return None
    value = getattr(payload, field, None)
    if value is not None:
        return value
    return (getattr(payload, "model_extra", None) or {}).get(field)


def _extensions(payload: Any) -> dict[str, Any]:
    """What the provider put on a message or a call beyond the fields the SDK knows."""
    return {
        name: value
        for name, value in (getattr(payload, "model_extra", None) or {}).items()
        if value is not None and name not in _NOT_AN_EXTENSION
    }


def _read_usage(payload: Any) -> Usage | None:
    if payload is None:
        return None
    details = getattr(payload, "prompt_tokens_details", None)
    cost = _extra(payload, "cost")
    return Usage(
        prompt_tokens=int(getattr(payload, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(payload, "completion_tokens", 0) or 0),
        cached_tokens=int(_extra(details, "cached_tokens") or 0),
        cache_write_tokens=int(_extra(details, "cache_write_tokens") or 0),
        cost=float(cost) if cost is not None else None,
    )


def _read_turn(response: Any) -> tuple[CompletionTurn | None, str]:
    if not response.choices:
        error = _extra(response, "error")
        return None, f"no choices: {error}" if error else "no choices and no error detail"

    choice = response.choices[0]
    message = choice.message
    tool_calls: list[ToolCall] = []
    for call in message.tool_calls or ():
        call_id = call.id or ""
        # An id the next model would refuse, or one this response already used, is
        # replaced here, before anything keeps it.
        if not _PORTABLE_ID.fullmatch(call_id) or any(c.id == call_id for c in tool_calls):
            call_id = f"call_{secrets.token_hex(12)}"
        tool_calls.append(
            ToolCall(
                id=call_id,
                name=call.function.name,
                arguments_json=call.function.arguments,
                extensions=_extensions(call),
            )
        )
    content = (message.content or "").strip()
    reason = getattr(choice, "finish_reason", None)
    if not content and not tool_calls and reason != "stop":
        return None, f"no content and no tool calls, finish_reason={reason}"
    usage = _read_usage(getattr(response, "usage", None))
    return CompletionTurn(content, tuple(tool_calls), usage, _extensions(message)), ""
