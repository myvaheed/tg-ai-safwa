"""The review nobody answered in time, then everything waiting as one turn, per tick — and
the rows that outlive a failed turn.

A Reminder needs none of this — its own `next_fire_at` is what makes it retry. A Cue row
is for the producer with nothing to fire twice, so the row *is* the retry: it is deleted
only once the answer reached the owner.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.poll import run_poll
from .queue import forget, settle, stamp, unstamp, waiting_cues

logger = logging.getLogger(__name__)

# Supplied by the runtime so this module stays free of the Advisor and the bot.
Gate = Callable[[], Awaitable[bool]]
Speaker = Callable[[str, str], Awaitable[bool]]
# Whether the message of the turn under this id reached the chat.
Delivered = Callable[[str], Awaitable[bool]]
LeaseRelease = Callable[[], None]
Expiry = Callable[[], Awaitable[None]]
# The words of a hook's request, made now from what it refers to; None drops the request.
Preparer = Callable[[str, list[Any]], Awaitable[str | None]]

# One row as the poll read it: id, the stamp, the words, the hook, what it refers to.
Row = tuple[int, str | None, str | None, str | None, list[Any]]


async def _nothing_expires() -> None:
    return None


async def _no_words(hook: str, payload: list[Any]) -> str | None:
    return None


@dataclass(slots=True)
class _Request:
    """What one turn says about one thing: a text row, or every row of one hook."""

    ids: list[int]
    text: str | None = None
    hook: str | None = None
    items: list[Any] = field(default_factory=list)

    def add(self, cue_id: int, items: list[Any]) -> None:
        self.ids.append(cue_id)
        self.items += [item for item in items if item not in self.items]


def _requests(rows: Iterable[Row]) -> list[_Request]:
    """One request per text row, and one per hook over all its rows, oldest first; a
    hook's items each once, in the order they were written."""
    requests: list[_Request] = []
    by_hook: dict[str, _Request] = {}
    for cue_id, _, text, hook, payload in rows:
        if hook is None:
            requests.append(_Request([cue_id], text=text))
            continue
        if hook not in by_hook:
            by_hook[hook] = _Request([], hook=hook)
            requests.append(by_hook[hook])
        by_hook[hook].add(cue_id, payload)
    return requests


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    delivered: Delivered,
    release: LeaseRelease = lambda: None,
    expire: Expiry = _nothing_expires,
    prepare: Preparer = _no_words,
) -> bool:
    """One poll. Returns whether a turn was delivered.

    The review that ran out of time is closed before the gate is read, so what was waiting
    behind that screen is said on this tick and not a later one. A turn an earlier tick
    started and did not finish is judged first, by whether its message reached the chat:
    its rows are settled if it did and wait again if not, and it is never said twice.
    Everything waiting by then is said in the one turn, oldest first, as one request: a
    hook's rows are worded once, together, once the gate is open, not on every poll the
    Advisor is busy for; one whose words cannot be made stays owed alone, and nothing to
    say settles it without a turn. The rows the turn says are stamped with its id before
    it starts, the message is registered under that id, and exactly those rows are settled
    once the answer landed — a row written meanwhile carries no stamp.
    """
    await expire()
    async with sessions() as session:
        rows: list[Row] = [
            (cue.id, cue.event_id, cue.text, cue.hook, list(cue.payload or []))
            for cue in await waiting_cues(session)
        ]
    landed = {
        event_id: await delivered(event_id)
        for event_id in {stamped for _, stamped, *_ in rows if stamped is not None}
    }
    if landed:
        async with sessions() as session:
            for event_id, reached in landed.items():
                await (settle if reached else unstamp)(session, event_id)
            await session.commit()
    requests = _requests(row for row in rows if not landed.get(row[1]))
    if not requests:
        return False
    if not await gate():
        # Nothing is deleted, so the rows stay and the next tick tries them again.
        return False
    try:
        said: list[int] = []
        texts: list[str] = []
        nothing: list[int] = []
        for request in requests:
            text = request.text
            if request.hook is not None:
                try:
                    text = await prepare(request.hook, request.items)
                except Exception:
                    logger.exception("The words of the hook %s could not be made", request.hook)
                    continue
                if text is None:
                    nothing += request.ids
                    continue
            said += request.ids
            texts.append(text or "")
        if nothing:
            async with sessions() as session:
                await forget(session, nothing)
                await session.commit()
        if not said:
            return False
        event_id = uuid4().hex
        async with sessions() as session:
            await stamp(session, said, event_id)
            await session.commit()
        if not await speak(event_id, "\n\n".join(texts)):
            # The stamp stays: the next tick asks whether the message reached the chat
            # after all, and settles or frees the rows by the answer.
            return False
        async with sessions() as session:
            await settle(session, event_id)
            await session.commit()
        return True
    finally:
        release()


async def run_cue_queue(
    sessions: async_sessionmaker[AsyncSession],
    *,
    gate: Gate,
    speak: Speaker,
    delivered: Delivered,
    release: LeaseRelease = lambda: None,
    expire: Expiry = _nothing_expires,
    prepare: Preparer = _no_words,
    poll_seconds: float,
) -> None:
    await run_poll(
        lambda: tick(
            sessions,
            gate=gate,
            speak=speak,
            delivered=delivered,
            release=release,
            expire=expire,
            prepare=prepare,
        ),
        poll_seconds=poll_seconds,
        name="The Cue poll",
    )
