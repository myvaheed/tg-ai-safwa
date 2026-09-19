"""Memory operations: what is remembered, and the one that takes a Sprint's analysis in.

An analysis is owed to memory until `Sprint.memory_at` says it was taken in, and the
poll finds the oldest one owed. The stamp is written in the transaction that writes the
Sprint's observations, so whatever ends the work before that — a failed call, the owner's
message, a restart — leaves the Sprint owed and the next poll does the same work again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_agent_shell.foundation.clock import utcnow

from ..retro.api import AnalysedSprint, analysed_sprints, mark_absorbed
from .absorb import Matcher, Observation, absorb, active
from .model import MemoryObservation, MemoryPattern
from .render import memory_text


class AbsorbResult(StrEnum):
    NOTHING = "nothing"
    BUSY = "busy"
    ABSORBED = "absorbed"


BackgroundRunner = Callable[
    [Callable[[Callable[[], bool]], Awaitable[AbsorbResult]]], Awaitable[AbsorbResult | None]
]


async def read_observations(session: AsyncSession) -> list[Observation]:
    rows = await session.scalars(select(MemoryObservation).order_by(MemoryObservation.id))
    return [Observation(row.sprint_id, row.pattern_id, row.text, row.raises) for row in rows]


async def write_observations(
    session: AsyncSession, sprint_id: int, observations: Sequence[Observation]
) -> None:
    """This Sprint's rows become these observations, no other Sprint's row is touched, and
    a pattern nothing observes any more goes with them."""
    await session.execute(
        delete(MemoryObservation).where(MemoryObservation.sprint_id == sprint_id)
    )
    for observation in observations:
        pattern_id = observation.pattern_id
        if pattern_id is None:
            pattern = MemoryPattern()
            session.add(pattern)
            await session.flush()
            pattern_id = pattern.id
        session.add(
            MemoryObservation(
                sprint_id=sprint_id,
                pattern_id=pattern_id,
                text=observation.text,
                raises=observation.raises,
            )
        )
    await session.flush()
    await session.execute(
        delete(MemoryPattern).where(
            MemoryPattern.id.not_in(select(MemoryObservation.pattern_id))
        )
    )


def taken_in(analysed: Sequence[AnalysedSprint]) -> list[AnalysedSprint]:
    """The Sprints whose observations count: taken in, in the order they ended."""
    return [sprint for sprint in analysed if sprint.memory_at is not None]


async def remembered(session: AsyncSession) -> str:
    """What Safwa remembers, as text: the patterns and the last analysed Sprint."""
    analysed = await analysed_sprints(session)
    patterns = active(await read_observations(session), taken_in(analysed))
    return memory_text(patterns, analysed[-1] if analysed else None)


@dataclass(frozen=True)
class Remembered:
    text: str


class MemoryReader:
    """What the Advisor's context builder reads memory through."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def sync(self) -> Remembered:
        async with self.sessions() as session:
            return Remembered(await remembered(session))


async def absorb_due(
    match: Matcher,
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_background: BackgroundRunner,
    now: datetime | None = None,
) -> AbsorbResult:
    """Take the oldest analysis still owed to memory in, under the background lease."""
    async with sessions() as session:
        if not any(sprint.memory_at is None for sprint in await analysed_sprints(session)):
            return AbsorbResult.NOTHING

    async def work(still_current: Callable[[], bool]) -> AbsorbResult:
        # Read again under the lease: an analysis written since is the one taken in, and
        # none can be written while this holds it.
        async with sessions() as session:
            analysed = await analysed_sprints(session)
            observations = await read_observations(session)
        owed = next((sprint for sprint in analysed if sprint.memory_at is None), None)
        if owed is None:
            return AbsorbResult.NOTHING
        ours = await absorb(observations, owed, taken_in(analysed), match)
        if not still_current():
            return AbsorbResult.BUSY
        async with sessions() as session:
            await write_observations(session, owed.id, ours)
            await mark_absorbed(session, owed.id, now or utcnow())
            await session.commit()
        return AbsorbResult.ABSORBED

    result = await run_background(work)
    return AbsorbResult.BUSY if result is None else result
