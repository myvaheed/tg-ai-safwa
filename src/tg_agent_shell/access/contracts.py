"""The access gate the container carries, without importing its implementation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from aiogram.types import Message


class ChatAccess(Protocol):
    @property
    def blocked(self) -> bool: ...

    @property
    def unlocking(self) -> bool: ...

    async def enabled(self) -> bool: ...

    async def initialize(self, anchor: Message) -> None: ...

    async def lock(self, anchor: Message, *, keep_unanswered: bool = True) -> None: ...

    async def clear(self, anchor: Message, *, keep_unanswered: bool = False) -> None: ...

    async def intercept(
        self, event: Any, again: Callable[[], Awaitable[Any]] | None = None
    ) -> bool: ...
