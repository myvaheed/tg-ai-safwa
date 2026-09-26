"""One-shot Diary operations shared by proposals and direct adapters."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.media.library import ChatMedia

from ...foundation.workspace import bump_workspace
from .model import DiaryEntry, DiaryMedia

# The change a hook may follow up on: a day was written or rewritten.
DIARY_WRITTEN = "diary.written"
# A day's photos are one album on its screen, and a Telegram album holds no more
# (TELEGRAM_ALBUM_LIMIT).
DIARY_DAY_PHOTOS = 10


async def diary_entry_for(session: AsyncSession, entry_date: date) -> DiaryEntry | None:
    return await session.scalar(select(DiaryEntry).where(DiaryEntry.entry_date == entry_date))


async def day_media(session: AsyncSession, entry_id: int) -> list[DiaryMedia]:
    """The photos on one day, in the order they were put there."""
    return list(
        await session.scalars(
            select(DiaryMedia).where(DiaryMedia.entry_id == entry_id).order_by(DiaryMedia.id)
        )
    )


def _validated_feeling_score(score: int | None) -> int | None:
    if score is not None and not 0 <= score <= 10:
        raise DomainError("A feeling score runs from 0 to 10")
    return score


def _words(body: str | None) -> str | None:
    """The day's words, or None to leave the words it has. Words that are all blank are none."""
    if body is None:
        return None
    text = body.strip()
    if not text:
        raise DomainError("A Diary entry cannot be empty")
    return text


async def _put_media(
    session: AsyncSession,
    entry: DiaryEntry,
    add: Sequence[tuple[int, str]],
    remove: Sequence[int],
) -> int:
    """Take photos off a day and put others on it; says how many the day holds after."""
    on_day = {row.media_id: row for row in await day_media(session, entry.id)}
    for media_id in remove:
        row = on_day.pop(media_id, None)
        if row is None:
            raise DomainError(f"Photo {media_id} is not on that day")
        await session.delete(row)
    for media_id, meta in add:
        if media_id in on_day:
            raise DomainError(f"Photo {media_id} is already on that day")
        if await session.get(ChatMedia, media_id) is None:
            raise DomainError(f"No photo has the number {media_id}")
        on_day[media_id] = DiaryMedia(entry_id=entry.id, media_id=media_id, meta=meta.strip())
        session.add(on_day[media_id])
    if len(on_day) > DIARY_DAY_PHOTOS:
        raise DomainError(f"A Diary day holds at most {DIARY_DAY_PHOTOS} photos")
    await session.flush()
    return len(on_day)


async def create_diary_entry(
    session: AsyncSession,
    *,
    entry_date: date,
    body: str | None,
    feeling_score: int | None = None,
    media: Sequence[tuple[int, str]] = (),
) -> DiaryEntry:
    """Start a day with its words, its photos, or both; a second one for the same date is an
    update."""
    text = _words(body)
    if text is None and not media:
        raise DomainError("A Diary day needs words or a photo")
    if await diary_entry_for(session, entry_date) is not None:
        raise DomainError("This day already has a Diary entry")
    entry = DiaryEntry(
        entry_date=entry_date, body=text, feeling_score=_validated_feeling_score(feeling_score)
    )
    session.add(entry)
    await session.flush()
    await _put_media(session, entry, media, ())
    record_change(session, DIARY_WRITTEN, entry.id)
    await bump_workspace(session)
    return entry


async def update_diary_entry(
    session: AsyncSession,
    entry_id: int,
    body: str | None,
    feeling_score: int | None = None,
    *,
    add_media: Sequence[tuple[int, str]] = (),
    remove_media: Sequence[int] = (),
) -> DiaryEntry:
    """Replace a day's words, keep them when `body` is None, and change its photos.

    Words are replaced whole, never patched. Which score reaches here is the caller's
    decision: the proposal handler carries the saved one forward when the model named none.
    A day left with neither words nor photos is removed.
    """
    entry = await session.get(DiaryEntry, entry_id)
    if entry is None:
        raise DomainError("Diary entry does not exist")
    text = _words(body)
    if text is not None:
        entry.body = text
    entry.feeling_score = _validated_feeling_score(feeling_score)
    photos = await _put_media(session, entry, add_media, remove_media)
    if entry.body is None and not photos:
        await session.delete(entry)
        await bump_workspace(session)
        return entry
    entry.version += 1
    record_change(session, DIARY_WRITTEN, entry.id)
    await bump_workspace(session)
    return entry


async def delete_diary_entry(session: AsyncSession, entry_id: int) -> None:
    """Remove a day outright, its photos with it; the Diary has no archive."""
    entry = await session.get(DiaryEntry, entry_id)
    if entry is None:
        raise DomainError("Diary entry does not exist")
    await session.execute(delete(DiaryMedia).where(DiaryMedia.entry_id == entry_id))
    await session.delete(entry)
    await bump_workspace(session)
