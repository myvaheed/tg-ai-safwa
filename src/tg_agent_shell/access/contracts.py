"""The access gate the container carries, without importing its implementation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from aiogram.types import BufferedInputFile, Message


class ChatAccess(Protocol):
    @property
    def blocked(self) -> bool: ...

    @property
    def unlocking(self) -> bool: ...

    async def enabled(self) -> bool: ...

    async def initialize(self, anchor: Message) -> None: ...

    async def lock(self, anchor: Message) -> None: ...

    async def intercept(self, event: Any) -> bool: ...

    async def defer(
        self, anchor: Message, payload: dict[str, Any], *, event_id: str | None = None
    ) -> bool: ...

    async def accepted(self, event_id: str) -> bool: ...

    async def photos(
        self, anchor: Message, pictures: Sequence[BufferedInputFile], *, kind: str
    ) -> None: ...
