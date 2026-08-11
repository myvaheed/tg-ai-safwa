from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from ..constants import AI_MAX_OUTPUT_TOKENS, AI_MAX_RETRIES_LOCAL, AI_TIMEOUT_SECONDS


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
            options["tool_choice"] = "auto"
        response = await self.client.chat.completions.create(**options)
        if not response.choices:
            raise RuntimeError("AI provider returned an empty response")
        message = response.choices[0].message
        tool_calls = tuple(
            ProviderToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in (message.tool_calls or [])
        )
        content = (message.content or "").strip()
        if not content and not tool_calls:
            raise RuntimeError("AI provider returned an empty response")
        return ProviderTurn(
            content=content,
            tool_calls=tool_calls,
            usage=_read_usage(getattr(response, "usage", None)),
        )

    async def close(self) -> None:
        await self.client.close()
