"""The small boundary used by the rest of an application."""

from __future__ import annotations

from typing import Protocol

from .model import CompletionRequest, CompletionTurn


class LlmProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionTurn: ...

    async def aclose(self) -> None: ...
