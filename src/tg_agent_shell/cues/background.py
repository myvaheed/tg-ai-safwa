"""The review nobody answered in time, then one waiting Cue, per tick — and the row that
outlives a failed turn.

A Reminder needs none of this — its own `next_fire_at` is what makes it retry. A Cue row
is for the producer with nothing to fire twice, so the row *is* the retry: it is deleted
only once the answer reached the owner.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.poll import run_poll
from .queue import next_cue, settle_cue

# Supplied by the runtime so this module stays free of the Advisor and the bot.
Gate = Callable[[], Awaitable[bool]]
Speaker = Callable[[str, str], Awaitable[bool]]
LeaseRelease = Callable[[], None]
Expiry = Callable[[], Awaitable[None]]
# The words of a hook's request, made now from what it refers to; None drops the request.
Preparer = Callable[[str, list[Any]], Awaitable[str | None]]


async def _nothing_expires() -> None:
    return None


async def _no_words(hook: str, payload: list[Any]) -> str | None:
    return None


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    release: LeaseRelease = lambda: None,
    expire: Expiry = _nothing_expires,
    prepare: Preparer = _no_words,
) -> bool:
    """One poll. Returns whether a Cue was delivered.

    The review that ran out of time is closed before the gate is read, so what was waiting
    behind that screen is said on this tick and not a later one. A hook's request is worded
    once the gate is open, not on every poll the Advisor is busy for; words that cannot be
    made leave the row for the next poll, and nothing to say settles it without a turn.
    """
    await expire()
    async with sessions() as session:
        cue = await next_cue(session)
        if cue is None:
            return False
        cue_id, event_id, text = cue.id, cue.event_id, cue.text
        hook, payload = cue.hook, list(cue.payload or [])
    if not await gate():
        # Nothing is deleted, so the row stays and the next tick tries it again.
        return False
    try:
        if hook is not None:
            text = await prepare(hook, payload)
            if text is None:
                await _settle(sessions, cue_id, payload)
                return False
        if not await speak(event_id, text):
            return False
        await _settle(sessions, cue_id, payload)
        return True
    finally:
        release()


async def _settle(
    sessions: async_sessionmaker[AsyncSession], cue_id: int, payload: list[Any]
) -> None:
    async with sessions() as session:
        await settle_cue(session, cue_id, payload)
        await session.commit()


async def run_cue_queue(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    release: LeaseRelease = lambda: None,
    expire: Expiry = _nothing_expires,
    prepare: Preparer = _no_words,
    poll_seconds: float,
) -> None:
    await run_poll(
        lambda: tick(
            sessions, gate=gate, speak=speak, release=release, expire=expire, prepare=prepare
        ),
        poll_seconds=poll_seconds,
        name="The Cue poll",
    )
