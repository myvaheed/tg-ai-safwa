"""The owner's actions, and their waits for an answer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.clock import Clock, SystemClock
from .use_cases import record_usage, usage_seconds


class UsageRecorder:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], owner_id: int, *,
        clock: Clock | None = None,
    ) -> None:
        self.sessions = sessions
        self.owner_id = owner_id
        self.clock = clock if clock is not None else SystemClock()
        # Concurrent events must not each add to the same total they read.
        self._writing = asyncio.Lock()
        self._waiting_since: ContextVar[datetime | None] = ContextVar(
            "usage_waiting_since", default=None,
        )

    async def _record(self, since: datetime, at: datetime) -> None:
        async with self._writing, self.sessions() as session:
            await record_usage(session, self.owner_id, since=since, at=at)
            await session.commit()

    async def acted(self, *, recording_seconds: int = 0) -> None:
        """An owner action; their own recording took its duration before it was sent."""
        now = self.clock.now()
        await self._record(now - timedelta(seconds=recording_seconds), now)

    @asynccontextmanager
    async def waiting(self) -> AsyncIterator[None]:
        """The owner waits while their event is handled, unless `answered` came first."""
        token = self._waiting_since.set(self.clock.now())
        try:
            yield
        finally:
            try:
                await self.answered()
            finally:
                self._waiting_since.reset(token)

    async def answered(self) -> None:
        """The owner has their answer; work after it is not their wait."""
        since = self._waiting_since.get()
        if since is None:
            return
        self._waiting_since.set(None)
        await self._record(since, self.clock.now())

    async def seconds(self, session: AsyncSession) -> int:
        return await usage_seconds(session, self.owner_id, now=self.clock.now())
