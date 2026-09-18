"""Writing a Cue, and reading what is waiting.

This module knows nothing about the bot or the Advisor: a producer writes into its own
transaction, and the delivery poll is somewhere else entirely.

A row is written once and never edited. Every change after that is one statement over a
set of rows named by id or by stamp — so two writers at once cannot lose each other's
rows, and a row whose id SQLite handed out again is never mistaken for the one deleted.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .model import Cue


async def add_cue(session: AsyncSession, *, text: str) -> Cue:
    """Queue something to be said, once the owner is not busy."""
    cue = Cue(text=text)
    session.add(cue)
    await session.flush()
    return cue


async def add_hook_cue(session: AsyncSession, *, hook: str, items: Sequence[Any]) -> Cue:
    """Write down one finding of a hook: what it refers to, never the words."""
    cue = Cue(hook=hook, payload=list(items))
    session.add(cue)
    await session.flush()
    return cue


async def drop_hook_cue(session: AsyncSession, hook: str) -> None:
    """Forget the hook's pending requests; nothing brings them back."""
    await session.execute(delete(Cue).where(Cue.hook == hook))


async def waiting_cues(session: AsyncSession) -> list[Cue]:
    """Every row, oldest first: the ones a turn stamped, and the ones still waiting."""
    return list(await session.scalars(select(Cue).order_by(Cue.id)))


async def words_waiting(session: AsyncSession) -> bool:
    """Whether a Cue written as words, not as a hook's request, is still waiting."""
    return await session.scalar(select(Cue.id).where(Cue.text.is_not(None)).limit(1)) is not None


async def forget(session: AsyncSession, ids: Sequence[int]) -> None:
    """These rows were found to have nothing to say: gone, unless a turn has them."""
    await session.execute(delete(Cue).where(Cue.id.in_(ids), Cue.event_id.is_(None)))


async def forget_before(session: AsyncSession, hooks: Sequence[str], moment: datetime) -> None:
    """These hooks' rows written before this moment are about a day that is over: gone,
    unless a turn has them."""
    await session.execute(
        delete(Cue).where(
            Cue.hook.in_(hooks), Cue.event_id.is_(None), Cue.created_at < moment
        )
    )


async def stamp(session: AsyncSession, ids: Sequence[int], event_id: str) -> None:
    """These rows are what one turn is about to say, under this id."""
    await session.execute(
        update(Cue).where(Cue.id.in_(ids), Cue.event_id.is_(None)).values(event_id=event_id)
    )


async def unstamp(session: AsyncSession, event_id: str) -> None:
    """The turn under this id did not reach the owner: its rows wait again."""
    await session.execute(update(Cue).where(Cue.event_id == event_id).values(event_id=None))


async def settle(session: AsyncSession, event_id: str) -> None:
    """The turn under this id reached the owner: its rows, and only they, are done."""
    await session.execute(delete(Cue).where(Cue.event_id == event_id))
