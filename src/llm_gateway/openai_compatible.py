"""Adapter for the OpenAI-compatible chat-completions API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from .model import CompletionRequest, CompletionTurn, ToolCall, Usage

logger = logging.getLogger(__name__)


OpenAICompatibleError = OpenAIError


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
    api_key: str
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
        options: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(request.messages),
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
        }
        if self.config.send_temperature and request.temperature is not None:
            options["temperature"] = request.temperature
        reasoning_effort = request.reasoning_effort or self.config.reasoning_effort
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        if request.response_schema and self.config.structured_output:
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
                if request.tool_choice and self.config.tool_choice_required
                else "auto"
            )

        detail = ""
        for attempt in range(1, self.config.empty_response_attempts + 1):
            response = await self.client.chat.completions.create(**options)
            turn, detail = _read_turn(response)
            if turn is not None:
                return turn
            if attempt < self.config.empty_response_attempts:
                logger.warning("LLM provider returned an empty response (%s); retrying", detail)
        raise RuntimeError(f"LLM provider returned an empty response: {detail}")

    async def aclose(self) -> None:
        await self.client.close()


def _extra(payload: Any, field: str) -> Any:
    if payload is None:
        return None
    value = getattr(payload, field, None)
    if value is not None:
        return value
    return (getattr(payload, "model_extra", None) or {}).get(field)


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
    tool_calls = tuple(
        ToolCall(
            id=call.id,
            name=call.function.name,
            arguments_json=call.function.arguments,
        )
        for call in (message.tool_calls or [])
    )
    content = (message.content or "").strip()
    reason = getattr(choice, "finish_reason", None)
    if not content and not tool_calls and reason != "stop":
        return None, f"no content and no tool calls, finish_reason={reason}"
    return CompletionTurn(content, tool_calls, _read_usage(getattr(response, "usage", None))), ""
