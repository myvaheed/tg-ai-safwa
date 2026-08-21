"""Test doubles for code that depends on :class:`LlmProvider`."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from .model import CompletionRequest, CompletionTurn


class ScriptedProvider:
    """Return predefined turns and retain each request for assertions."""

    def __init__(self, turns: Iterable[CompletionTurn]) -> None:
        self._turns = deque(turns)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("The application made an unexpected LLM request")
        return self._turns.popleft()

    async def aclose(self) -> None:
        return None
