from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from .model import IDLE_WINDOW, OwnerUsage


def _elapsed(start: datetime, end: datetime) -> timedelta:
    return max(end - start, timedelta())


async def record_usage(
    session: AsyncSession, owner_id: int, *, since: datetime, at: datetime,
) -> None:
    """Count `since` to `at` and the quiet window before it, each moment once.

    `last_at` only moves forward, so what it passed is never counted again.
    """
    usage = await session.get(OwnerUsage, owner_id)
    if usage is None:
        session.add(OwnerUsage(
            owner_id=owner_id, seconds=_elapsed(since, at).total_seconds(), last_at=at,
        ))
        return
    quiet = min(_elapsed(usage.last_at, since), IDLE_WINDOW)
    usage.seconds += (quiet + _elapsed(max(since, usage.last_at), at)).total_seconds()
    usage.last_at = max(usage.last_at, at)


async def usage_seconds(session: AsyncSession, owner_id: int, *, now: datetime) -> int:
    usage = await session.get(OwnerUsage, owner_id)
    if usage is None:
        return 0
    quiet = min(_elapsed(usage.last_at, now), IDLE_WINDOW)
    return int(usage.seconds + quiet.total_seconds())
