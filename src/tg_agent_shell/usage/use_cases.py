from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .model import IDLE_WINDOW, UsageInterval


def covered_seconds(intervals: Iterable[tuple[datetime, datetime]], *, now: datetime) -> int:
    """Count the union of elapsed intervals; future grace time is never prepaid."""
    total = timedelta()
    edge: datetime | None = None
    for start, end in sorted(intervals):
        end = min(end, now)
        start = max(start, edge) if edge is not None else start
        if end > start:
            total += end - start
            edge = end
    return int(total.total_seconds())


async def record_activity(
    session: AsyncSession, *, owner_id: int, event_key: str, at: datetime,
    voice_seconds: int = 0,
) -> None:
    await session.execute(
        insert(UsageInterval).values(
            owner_id=owner_id, event_key=f"activity:{event_key}",
            started_at=at - timedelta(seconds=voice_seconds), ended_at=at + IDLE_WINDOW,
            active=False,
        ).on_conflict_do_nothing(index_elements=["owner_id", "event_key"])
    )


async def start_processing(
    session: AsyncSession, *, owner_id: int, event_key: str, at: datetime,
) -> int | None:
    return await session.scalar(
        insert(UsageInterval).values(
            owner_id=owner_id, event_key=f"processing:{event_key}",
            started_at=at, ended_at=at, active=True,
        ).on_conflict_do_nothing(index_elements=["owner_id", "event_key"])
        .returning(UsageInterval.id)
    )


async def checkpoint_processing(session: AsyncSession, interval_id: int, *, at: datetime) -> bool:
    return await session.scalar(
        update(UsageInterval).where(UsageInterval.id == interval_id, UsageInterval.active.is_(True))
        .values(ended_at=at).returning(UsageInterval.id)
    ) is not None


async def finish_processing(
    session: AsyncSession, *, owner_id: int, event_key: str, at: datetime, reading: bool,
) -> None:
    await session.execute(
        update(UsageInterval).where(
            UsageInterval.owner_id == owner_id,
            UsageInterval.event_key == f"processing:{event_key}",
            UsageInterval.active.is_(True),
        )
        .values(ended_at=at + IDLE_WINDOW if reading else at, active=False)
    )


async def recover_usage(session: AsyncSession) -> None:
    # The last checkpoint is confirmed work; the time until restart is not.
    await session.execute(
        update(UsageInterval).where(UsageInterval.active.is_(True)).values(active=False)
    )


async def usage_seconds(session: AsyncSession, owner_id: int, *, now: datetime) -> int:
    rows = (await session.execute(
        select(UsageInterval.started_at, UsageInterval.ended_at, UsageInterval.active)
        .where(UsageInterval.owner_id == owner_id)
    )).all()
    return covered_seconds(
        ((start, max(end, now) if active else end) for start, end, active in rows), now=now,
    )
