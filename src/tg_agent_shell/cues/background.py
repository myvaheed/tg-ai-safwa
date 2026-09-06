"""One waiting Cue per tick, and the row that outlives a failed turn.

A Reminder needs none of this — its own `next_fire_at` is what makes it retry. A Cue row
is for the producer with nothing to fire twice, so the row *is* the retry: it is deleted
only once the answer reached the owner.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.poll import run_poll
from .model import Cue
from .queue import next_cue

# Supplied by the runtime so this module stays free of the Advisor and the bot.
Gate = Callable[[], Awaitable[bool]]
Speaker = Callable[[str, str], Awaitable[bool]]
LeaseRelease = Callable[[], None]


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    release: LeaseRelease = lambda: None,
) -> bool:
    """One poll. Returns whether a Cue was delivered."""
    async with sessions() as session:
        cue = await next_cue(session)
        if cue is None:
            return False
        cue_id, event_id, text = cue.id, cue.event_id, cue.text
    if not await gate():
        # Nothing is deleted, so the row stays and the next tick tries it again.
        return False
    try:
        if not await speak(event_id, text):
            return False
        async with sessions() as session:
            delivered = await session.get(Cue, cue_id)
            if delivered is not None:
                await session.delete(delivered)
            await session.commit()
        return True
    finally:
        release()


async def run_cue_queue(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    release: LeaseRelease = lambda: None,
    poll_seconds: float,
) -> None:
    await run_poll(
        lambda: tick(sessions, gate=gate, speak=speak, release=release),
        poll_seconds=poll_seconds,
        name="The Cue poll",
    )
