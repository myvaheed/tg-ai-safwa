"""What the retro reads and writes on a Sprint that has closed: the owner's word on its
Success criteria, the analysis its last run left, and what an analysis is read from — the
Sprints that ended before it and the Diary of its days."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ..diary.model import DiaryEntry
from ..planning.closing import RetroStatistics
from ..planning.model import Sprint

# How many of the Sprints that ended before this one the analysis compares it with.
RETRO_SPRINTS_BEFORE = 2


@dataclass(frozen=True, slots=True)
class SprintColumn:
    """One Sprint as the analysis compares it: its record, its criteria and the owner's word."""

    number: str
    first: date
    last: date
    criteria: str
    met: bool | None
    statistics: RetroStatistics


@dataclass(frozen=True, slots=True)
class DiaryDay:
    day: date
    score: int | None
    body: str


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    """What one analysis is read from: the Sprints, oldest first and this one last, and the
    Diary of this Sprint's days as it reads today."""

    sprints: tuple[SprintColumn, ...]
    diary: dict[date, DiaryDay]

    @property
    def sprint(self) -> SprintColumn:
        return self.sprints[-1]


async def require_ended_sprint(session: AsyncSession, sprint_id: int) -> Sprint:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None:
        raise DomainError("Sprint does not exist")
    if sprint.retro is None:
        raise DomainError("The Sprint has not ended; its retro is written when it does")
    return sprint


async def mark_criterion(session: AsyncSession, sprint_id: int, met: bool | None) -> Sprint:
    """The owner's word on whether the Success criteria were met: yes, no, or not yet said."""
    sprint = await require_ended_sprint(session, sprint_id)
    sprint.criterion_met = met
    return sprint


async def record_analysis(session: AsyncSession, sprint_id: int, record: dict[str, Any]) -> Sprint:
    """Keep what a run made of the Sprint, in place of whatever an earlier run left, with
    the owner's mark as the run read it: a mark changed since is not what was analysed."""
    sprint = await require_ended_sprint(session, sprint_id)
    sprint.analysis = {
        **record,
        "met": sprint.criterion_met,
        "analysed_at": utcnow().isoformat(),
        "remembered": False,
    }
    return sprint


async def mark_remembered(session: AsyncSession, sprint_id: int) -> str:
    """The fact the analysis drew went to memory; the screen stops offering it. Returns it."""
    sprint = await require_ended_sprint(session, sprint_id)
    if sprint.analysis is None or not sprint.analysis.get("memory_fact"):
        raise DomainError("This analysis drew no fact to remember")
    if sprint.analysis.get("remembered"):
        raise DomainError("That fact is already in memory")
    sprint.analysis = {**sprint.analysis, "remembered": True}
    return str(sprint.analysis["memory_fact"])


async def analysis_input(session: AsyncSession, sprint_id: int) -> AnalysisInput:
    """Everything the analysis of this Sprint reads, read once and closed."""
    sprint = await require_ended_sprint(session, sprint_id)
    columns = tuple(_column(row) for row in [*await sprints_before(session, sprint), sprint])
    this = columns[-1]
    diary = {
        day: DiaryDay(day=day, score=entry.feeling_score, body=entry.body)
        for day, entry in (await diary_between(session, this.first, this.last)).items()
    }
    return AnalysisInput(sprints=columns, diary=diary)


def _column(sprint: Sprint) -> SprintColumn:
    statistics = RetroStatistics.from_record(sprint.retro)
    return SprintColumn(
        number=sprint.number,
        first=statistics.first_day,
        last=statistics.last_day,
        criteria=sprint.success_criteria,
        met=sprint.criterion_met,
        statistics=statistics,
    )


async def sprints_before(session: AsyncSession, sprint: Sprint) -> list[Sprint]:
    """The Sprints that ended before this one, oldest first, at most `RETRO_SPRINTS_BEFORE`."""
    rows = list(
        await session.scalars(
            select(Sprint)
            .where(Sprint.retro.is_not(None), Sprint.actual_ended_at < sprint.actual_ended_at)
            .order_by(Sprint.actual_ended_at.desc())
            .limit(RETRO_SPRINTS_BEFORE)
        )
    )
    return rows[::-1]


async def diary_between(session: AsyncSession, first: date, last: date) -> dict[date, DiaryEntry]:
    """The Diary as it reads today, one entry per day that has one."""
    entries = await session.scalars(
        select(DiaryEntry).where(DiaryEntry.entry_date >= first, DiaryEntry.entry_date <= last)
    )
    return {entry.entry_date: entry for entry in entries}
