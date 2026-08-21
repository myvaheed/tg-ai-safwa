"""One-shot Diary operations shared by proposals and direct adapters."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import DiaryEntry


async def diary_entry_for(session: AsyncSession, entry_date: date) -> DiaryEntry | None:
    return await session.scalar(select(DiaryEntry).where(DiaryEntry.entry_date == entry_date))


def _validated_feeling_score(score: int | None) -> int | None:
    if score is not None and not 0 <= score <= 10:
        raise DomainError("A feeling score runs from 0 to 10")
    return score


async def create_diary_entry(
    session: AsyncSession, *, entry_date: date, body: str, feeling_score: int | None = None
) -> DiaryEntry:
    """Write a day's first entry; a second one for the same date is an update."""
    text = body.strip()
    if not text:
        raise DomainError("A Diary entry cannot be empty")
    if await diary_entry_for(session, entry_date) is not None:
        raise DomainError("This day already has a Diary entry")
    entry = DiaryEntry(
        entry_date=entry_date, body=text, feeling_score=_validated_feeling_score(feeling_score)
    )
    session.add(entry)
    await session.flush()
    await bump_workspace(session)
    return entry


async def update_diary_entry(
    session: AsyncSession, entry_id: int, body: str, feeling_score: int | None = None
) -> DiaryEntry:
    """Replace a day's entry. The Diary is rewritten whole, never patched — the score with it."""
    entry = await session.get(DiaryEntry, entry_id)
    if entry is None:
        raise DomainError("Diary entry does not exist")
    text = body.strip()
    if not text:
        raise DomainError("A Diary entry cannot be empty")
    entry.body = text
    entry.feeling_score = _validated_feeling_score(feeling_score)
    entry.version += 1
    await bump_workspace(session)
    return entry


async def delete_diary_entry(session: AsyncSession, entry_id: int) -> None:
    """Remove a day's entry outright; the Diary has no archive."""
    entry = await session.get(DiaryEntry, entry_id)
    if entry is None:
        raise DomainError("Diary entry does not exist")
    await session.delete(entry)
    await bump_workspace(session)
