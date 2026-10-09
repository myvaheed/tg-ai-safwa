"""The access gate the container carries, without importing its implementation."""

from __future__ import annotations

from typing import Any, Protocol

from aiogram.types import Message


class ChatAccess(Protocol):
    @property
    def blocked(self) -> bool: ...

    @property
    def unlocking(self) -> bool: ...

    async def enabled(self) -> bool: ...

    async def initialize(self, anchor: Message) -> None: ...

    async def lock(self, anchor: Message) -> None: ...

    async def clear(self, anchor: Message) -> None: ...

    async def restore(self, anchor: Message) -> None: ...

    async def intercept(self, event: Any) -> bool: ...
