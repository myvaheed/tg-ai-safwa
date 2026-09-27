"""What another feature may ask of the Diary: the days written, never how they are written."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import DiaryEntry


async def last_entries(session: AsyncSession, limit: int) -> list[DiaryEntry]:
    """The days written last that carry words, newest first; a day of photos alone is not one."""
    return list(
        await session.scalars(
            select(DiaryEntry)
            .where(DiaryEntry.body.is_not(None))
            .order_by(DiaryEntry.entry_date.desc())
            .limit(limit)
        )
    )
