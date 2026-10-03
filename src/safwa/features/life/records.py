"""What Life in weeks reads, from the features that keep it, as the owner's local days."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.workspace import require_workspace
from ..cards.api import every_finished_action
from ..diary.api import feeling_scores, first_diary_day
from ..planning.api import sprint_day
from ..planning.model import Sprint
from ..profile.api import effort_tracking_on
from ..values.api import value_names


@dataclass(frozen=True, slots=True)
class LifeAction:
    """One finished Action, on the local day it was finished."""

    day: date
    effort: float | None
    categories: frozenset[str]
    energy_types: frozenset[str]
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class SprintSpan:
    """One Sprint, from its first local day to its last, or to today while it runs."""

    number: str
    first: date
    last: date
    met: bool | None
    # Which day of how many the running Sprint is on; None once it ended.
    running_day: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class LifeRecords:
    today: date
    # The first day anything was recorded: the day Safwa was first started, or the first day
    # of the Diary when that is earlier.
    since: date
    feelings: Mapping[date, int]
    actions: tuple[LifeAction, ...]
    sprints: tuple[SprintSpan, ...]
    # Every Value by name, served or not.
    values: tuple[str, ...]
    effort_tracking: bool


async def local_today(session: AsyncSession, now: datetime) -> date:
    workspace = await require_workspace(session)
    return now.astimezone(ZoneInfo(workspace.timezone)).date()


async def life_records(session: AsyncSession, now: datetime) -> LifeRecords:
    workspace = await require_workspace(session)
    zone = ZoneInfo(workspace.timezone)
    today = now.astimezone(zone).date()
    started = workspace.created_at.astimezone(zone).date()
    first_day = await first_diary_day(session)
    names = await value_names(session)
    actions = tuple(
        LifeAction(
            day=action.completed_at.astimezone(zone).date(),
            effort=action.effort_points,
            categories=action.categories,
            energy_types=action.energy_types,
            values=frozenset(names[value_id] for value_id in action.value_ids if value_id in names),
        )
        for action in await every_finished_action(session)
    )
    sprints = tuple(
        SprintSpan(
            number=sprint.number,
            first=sprint.actual_started_at.astimezone(zone).date(),
            last=(
                sprint.actual_ended_at.astimezone(zone).date()
                if sprint.actual_ended_at is not None
                else today
            ),
            met=sprint.criterion_met,
            running_day=sprint_day(sprint, today) if sprint.actual_ended_at is None else None,
        )
        for sprint in await session.scalars(select(Sprint).order_by(Sprint.id))
    )
    return LifeRecords(
        today=today,
        since=min(started, first_day) if first_day is not None else started,
        feelings=await feeling_scores(session),
        actions=actions,
        sprints=sprints,
        values=tuple(sorted(names.values(), key=str.casefold)),
        effort_tracking=await effort_tracking_on(session),
    )
