"""The review nobody answered in time, then everything waiting as one turn, per tick — and
the rows that outlive a failed turn.

A Reminder needs none of this — its own `next_fire_at` is what makes it retry. A Cue row
is for the producer with nothing to fire twice, so the row *is* the retry: it is deleted
only once the answer reached the owner.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.poll import run_poll
from .queue import settle_cue, waiting_cues

logger = logging.getLogger(__name__)

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
    """One poll. Returns whether a turn was delivered.

    The review that ran out of time is closed before the gate is read, so what was waiting
    behind that screen is said on this tick and not a later one. Everything waiting by then
    is said in the one turn, oldest first, as one request: a hook's request is worded once
    the gate is open, not on every poll the Advisor is busy for; one whose words cannot be
    made stays owed alone, and nothing to say settles it without a turn. The message is
    registered under the oldest request said, and what was said with it is settled with it.
    """
    await expire()
    async with sessions() as session:
        waiting = [
            (cue.id, cue.event_id, cue.text, cue.hook, list(cue.payload or []))
            for cue in await waiting_cues(session)
        ]
    if not waiting:
        return False
    if not await gate():
        # Nothing is deleted, so the rows stay and the next tick tries them again.
        return False
    try:
        said: list[tuple[int, list[Any]]] = []
        texts: list[str] = []
        for cue_id, _, text, hook, payload in waiting:
            if hook is not None:
                try:
                    text = await prepare(hook, payload)
                except Exception:
                    logger.exception("The words of the hook %s could not be made", hook)
                    continue
                if text is None:
                    await _settle(sessions, [(cue_id, payload)])
                    continue
            said.append((cue_id, payload))
            texts.append(text or "")
        if not said:
            return False
        event_id = next(row[1] for row in waiting if row[0] == said[0][0])
        if not await speak(event_id, "\n\n".join(texts)):
            return False
        await _settle(sessions, said)
        return True
    finally:
        release()


async def _settle(
    sessions: async_sessionmaker[AsyncSession], rows: list[tuple[int, list[Any]]]
) -> None:
    async with sessions() as session:
        for cue_id, payload in rows:
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
