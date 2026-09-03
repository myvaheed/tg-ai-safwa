"""Continuity operations: what is recorded, and when maintenance is due.

Each takes the session or the session factory its caller already owns.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..profile.api import scheduled_memory_time
from .model import MemorySyncState
from .persona import BackgroundMemoryRunner, MemoryMaintenanceResult, PersonaContinuity

logger = logging.getLogger(__name__)


async def run_due_memory_maintenance(
    continuity: PersonaContinuity,
    sessions: async_sessionmaker[AsyncSession],
    chat_id: int,
    timezone: str,
    *,
    run_background: BackgroundMemoryRunner,
    now: datetime | None = None,
) -> bool:
    """Run the configured once-daily memory sync if it is due."""
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    async with sessions() as session:
        update_time = await scheduled_memory_time(session)
        state = await session.get(MemorySyncState, 1)
        if update_time is None or local_now.time().replace(tzinfo=None) < update_time:
            return False
        if state and state.memory_last_run_at:
            last_run = state.memory_last_run_at
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            if last_run.astimezone(zone).date() >= local_now.date():
                return False

    result = await run_background(
        lambda still_current: continuity.maintain_memory(
            chat_id,
            still_current=still_current,
        )
    )
    if result not in {MemoryMaintenanceResult.UPDATED, MemoryMaintenanceResult.CURRENT}:
        return False
    await record_memory_run(sessions, local_now.astimezone(UTC))
    return True


async def record_memory_run(
    sessions: async_sessionmaker[AsyncSession],
    occurred_at: datetime | None = None,
) -> None:
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        if state is None:
            state = MemorySyncState(id=1)
            session.add(state)
        state.memory_last_run_at = occurred_at or datetime.now(UTC)
        await session.commit()
