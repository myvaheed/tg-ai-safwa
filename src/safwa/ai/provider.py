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
        response = await self.client.chat.completions.create(**options)
        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("AI provider returned an empty response")
        return response.choices[0].message.content.strip()

    async def close(self) -> None:
        await self.client.close()
