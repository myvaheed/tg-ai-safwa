"""What another feature may ask of the Diary: the days written, never how they are written."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.media.library import ChatMedia

from .model import DiaryEntry, DiaryMedia


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


async def day_media(session: AsyncSession, entry_id: int) -> list[tuple[int, str]]:
    """The photos on one day, each by its number and its label, in the order they were put
    there."""
    rows = await session.execute(
        select(DiaryMedia.media_id, ChatMedia.meta)
        .join(ChatMedia, ChatMedia.id == DiaryMedia.media_id)
        .where(DiaryMedia.entry_id == entry_id)
        .order_by(DiaryMedia.id)
    )
    return [(media_id, meta) for media_id, meta in rows]


async def feeling_scores(session: AsyncSession) -> dict[date, int]:
    """Every day that said how it felt, by its date."""
    rows = await session.execute(
        select(DiaryEntry.entry_date, DiaryEntry.feeling_score).where(
            DiaryEntry.feeling_score.is_not(None)
        )
    )
    return {day: int(score) for day, score in rows}


async def first_diary_day(session: AsyncSession) -> date | None:
    """The earliest day the Diary holds, if it holds one."""
    return await session.scalar(select(func.min(DiaryEntry.entry_date)))
