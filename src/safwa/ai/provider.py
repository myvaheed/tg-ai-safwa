from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from ..constants import (
    AI_EMPTY_RESPONSE_ATTEMPTS,
    AI_MAX_OUTPUT_TOKENS,
    AI_MAX_RETRIES_LOCAL,
    AI_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = AI_TIMEOUT_SECONDS
    max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    structured_output: bool = False
    max_retries: int = AI_MAX_RETRIES_LOCAL
    # Reasoning models such as openai/gpt-5.6-luna do not accept `temperature`.
    send_temperature: bool = True
    tool_choice_required: bool = True
    reasoning_effort: str | None = None
    # Tuples keep the dataclass hashable.
    default_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float | None = None


@dataclass(frozen=True)
class ProviderTurn:
    content: str
    tool_calls: tuple[ProviderToolCall, ...] = ()
    usage: ProviderUsage | None = None


def _extra(payload: Any, field: str) -> Any:
    """Read a field the OpenAI SDK models do not declare.

    ``cache_write_tokens`` and ``cost`` are OpenRouter additions; a local server
    sends neither, so a missing field is not an error.
    """

    if payload is None:
        return None
    value = getattr(payload, field, None)
    if value is not None:
        return value
    extra = getattr(payload, "model_extra", None) or {}
    return extra.get(field)


def _read_usage(payload: Any) -> ProviderUsage | None:
    if payload is None:
        return None
    details = getattr(payload, "prompt_tokens_details", None)
    cost = _extra(payload, "cost")
    return ProviderUsage(
        prompt_tokens=int(getattr(payload, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(payload, "completion_tokens", 0) or 0),
        cached_tokens=int(_extra(details, "cached_tokens") or 0),
        cache_write_tokens=int(_extra(details, "cache_write_tokens") or 0),
        cost=float(cost) if cost is not None else None,
    )


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            default_headers=dict(config.default_headers) or None,
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> str:
        turn = await self.complete_turn(
            messages,
            json_schema=json_schema,
            temperature=temperature,
        )
        if not turn.content:
            raise RuntimeError("AI provider returned an empty response")
        return turn.content

    async def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> ProviderTurn:
        options: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
        }
        if self.config.send_temperature:
            options["temperature"] = temperature
        if self.config.reasoning_effort:
            options["reasoning_effort"] = self.config.reasoning_effort
        if json_schema and self.config.structured_output:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "safwa_response", "strict": True, "schema": json_schema},
            }
        if tools:
            options["tools"] = tools
            options["tool_choice"] = (
                tool_choice if tool_choice and self.config.tool_choice_required else "auto"
            )
        detail = ""
        for attempt in range(1, AI_EMPTY_RESPONSE_ATTEMPTS + 1):
            response = await self.client.chat.completions.create(**options)
            turn, detail = _read_turn(response)
            if turn is not None:
                return turn
            if attempt < AI_EMPTY_RESPONSE_ATTEMPTS:
                logger.warning(
                    "AI provider returned an empty response (%s); retrying once", detail
                )
        raise RuntimeError(f"AI provider returned an empty response: {detail}")

    async def close(self) -> None:
        await self.client.close()


def _read_turn(response: Any) -> tuple[ProviderTurn | None, str]:
    """Return the turn, or ``None`` plus why the provider said nothing."""
    if not response.choices:
        # OpenRouter reports an upstream failure as HTTP 200 with no choices and the
        # reason in an `error` member the OpenAI schema does not model.
        error = getattr(response, "error", None) or (
            getattr(response, "model_extra", None) or {}
        ).get("error")
        return None, f"no choices: {error}" if error else "no choices and no error detail"
    message = response.choices[0].message
    tool_calls = tuple(
        ProviderToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
        for call in (message.tool_calls or [])
    )
    content = (message.content or "").strip()
    reason = getattr(response.choices[0], "finish_reason", None)
    if not content and not tool_calls and reason != "stop":
        # `stop` with no content is a turn, not an answer: the session asks the model
        # again for words.  Any other reason means it was cut off mid-turn.
        return None, f"no content and no tool calls, finish_reason={reason}"
    return (
        ProviderTurn(
            content=content,
            tool_calls=tool_calls,
            usage=_read_usage(getattr(response, "usage", None)),
        ),
        "",
    )
