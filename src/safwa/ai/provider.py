from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 120.0
    max_output_tokens: int = 4096
    structured_output: bool = False


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderTurn:
    content: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout_seconds,
            max_retries=1,
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
            "temperature": temperature,
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
        }
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
        return ProviderTurn(content=content, tool_calls=tool_calls)

    async def close(self) -> None:
        await self.client.close()
