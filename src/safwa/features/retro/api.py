"""What another feature may ask of Retro: the Sprints an analysis was written for, and the
word that memory took one in.

Memory reads the analyses through this door and nothing else of the Sprint: which Sprint
is owed to it, and the record it takes in, are answered here without the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..planning.closing import RetroStatistics
from ..planning.model import Sprint


@dataclass(frozen=True, slots=True)
class AnalysedSprint:
    """One Sprint's analysis as memory reads it: the record, the days it covers, and
    whether memory has taken it in."""

    id: int
    number: str
    first: date
    last: date
    analysis: dict[str, Any]
    memory_at: datetime | None

    @property
    def met(self) -> bool | None:
        """The owner's mark on the Success criteria as the run read it."""
        return self.analysis.get("met")


async def analysed_sprints(session: AsyncSession) -> list[AnalysedSprint]:
    """Every Sprint an analysis was written for, in the order they ended."""
    rows = await session.scalars(
        select(Sprint)
        .where(Sprint.analysis.is_not(None))
        .order_by(Sprint.actual_ended_at, Sprint.id)
    )
    analysed = []
    for row in rows:
        statistics = RetroStatistics.from_record(row.retro)
        analysed.append(
            AnalysedSprint(
                id=row.id,
                number=row.number,
                first=statistics.first_day,
                last=statistics.last_day,
                analysis=row.analysis or {},
                memory_at=row.memory_at,
            )
        )
    return analysed


async def mark_absorbed(session: AsyncSession, sprint_id: int, at: datetime) -> None:
    """Memory took this Sprint's analysis in: the poll stops finding it."""
    sprint = await session.get(Sprint, sprint_id)
    if sprint is not None:
        sprint.memory_at = at
