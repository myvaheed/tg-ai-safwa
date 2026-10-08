"""The lifetime of one owner event's work, with a checkpoint while it waits."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.clock import Clock, SystemClock
from .use_cases import (
    checkpoint_processing,
    finish_processing,
    record_activity,
    start_processing,
    usage_seconds,
)

CHECKPOINT_SECONDS = 30
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Work:
    interval_id: int
    event_key: str
    started_at: datetime
    started_tick: float
    task: asyncio.Task[Any] | None
    timer: asyncio.Task[None] | None = None


class UsageRecorder:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], owner_id: int, *,
        clock: Clock | None = None, ticks: Callable[[], float] = monotonic,
    ) -> None:
        self.sessions = sessions
        self.owner_id = owner_id
        self.clock = clock if clock is not None else SystemClock()
        self.ticks = ticks
        self._current: ContextVar[_Work | None] = ContextVar(
            "usage_work", default=None,
        )

    async def activity(self, event_key: str, *, at: datetime, voice_seconds: int = 0) -> None:
        async with self.sessions() as session:
            await record_activity(
                session, owner_id=self.owner_id, event_key=event_key, at=at,
                voice_seconds=voice_seconds,
            )
            await session.commit()

    async def seconds(self, session: AsyncSession) -> int:
        return await usage_seconds(session, self.owner_id, now=self.clock.now())

    def _at(self, work: _Work) -> datetime:
        return work.started_at + timedelta(seconds=max(0, self.ticks() - work.started_tick))

    async def _checkpoint(self, work: _Work) -> bool:
        async with self.sessions() as session:
            active = await checkpoint_processing(session, work.interval_id, at=self._at(work))
            await session.commit()
            return active

    async def _heartbeat(self, work: _Work) -> None:
        while True:
            await asyncio.sleep(CHECKPOINT_SECONDS)
            try:
                if not await self._checkpoint(work):
                    return
            except Exception:
                logger.exception("Could not checkpoint usage for interval %s", work.interval_id)

    async def finish(self, *, reading: bool = True) -> None:
        work = self._current.get()
        if work is None or work.task is not asyncio.current_task():
            return
        if work.timer is not None:
            work.timer.cancel()
            await asyncio.gather(work.timer, return_exceptions=True)
        async with self.sessions() as session:
            await finish_processing(
                session, owner_id=self.owner_id, event_key=work.event_key,
                at=self._at(work), reading=reading,
            )
            await session.commit()
        self._current.set(None)

    @asynccontextmanager
    async def processing(self, event_key: str) -> AsyncIterator[None]:
        started = self.clock.now()
        tick = self.ticks()
        work = None
        token = None
        reading = False
        try:
            async with self.sessions() as session:
                interval_id = await start_processing(
                    session, owner_id=self.owner_id, event_key=event_key, at=started,
                )
                if interval_id is not None:
                    # Own the cleanup before commit or session exit can be cancelled.
                    work = _Work(interval_id, event_key, started, tick, asyncio.current_task())
                    token = self._current.set(work)
                await session.commit()
            if work is not None:
                work.timer = asyncio.create_task(self._heartbeat(work), name="usage-checkpoint")
            yield
            reading = True
        finally:
            if token is not None:
                try:
                    await self.finish(reading=reading)
                finally:
                    self._current.reset(token)
