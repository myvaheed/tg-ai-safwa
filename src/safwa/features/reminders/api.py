"""What another feature may ask of Reminders.

A Reminder the owner never created still belongs here: another feature says what it wants
said and when, and Reminders keeps the row, its schedule and its arithmetic.
"""

from __future__ import annotations

from datetime import UTC, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import WEEKDAY_NAMES
from ...enums import ScheduleKind
from ...foundation.clock import Clock
from ...foundation.errors import DomainError
from ...foundation.models import Workspace
from .model import Reminder
from .schedule import Schedule, next_fire, parse_clock, schedule_columns


def parse_clock_or_off(raw: str) -> time | None:
    """A daily Settings clock. ``off`` is None, which is how that setting is switched off."""
    return None if raw.strip().lower() == "off" else parse_clock(raw)


async def sync_daily_system_reminder(
    session: AsyncSession, *, instruction: str, at_time: time | None, clock: Clock
) -> Reminder | None:
    """Reconcile the one daily Reminder no owner created, and return it.

    `at_time` of None removes it. The owner's own Reminders and a Sprint's Reminders are
    never selected: the row this reconciles is the system one that belongs to no Sprint.
    """
    existing = await session.scalar(
        select(Reminder).where(Reminder.system.is_(True), Reminder.sprint_id.is_(None))
    )
    if at_time is None:
        if existing is not None:
            await session.delete(existing)
        return None

    if existing is not None and existing.at_time == at_time:
        # Same firing, so only the words can have changed; rescheduling would move a
        # Reminder that is already due at the right moment.
        if existing.instruction != instruction:
            existing.instruction = instruction
            existing.version += 1
        return existing

    workspace = await session.get(Workspace, 1)
    tz = ZoneInfo(workspace.timezone if workspace else "UTC")
    schedule = Schedule(kind=ScheduleKind.DAILY, weekdays=WEEKDAY_NAMES, at_time=at_time)
    first = next_fire(schedule, previous=None, now=clock.now().astimezone(UTC), tz=tz)
    if first is None:
        raise DomainError("That schedule has no future occurrence")

    if existing is None:
        created = Reminder(
            instruction=instruction,
            system=True,
            sprint_id=None,
            next_fire_at=first,
            **schedule_columns(schedule),
        )
        session.add(created)
        await session.flush()
        return created

    existing.instruction = instruction
    for column, value in schedule_columns(schedule).items():
        setattr(existing, column, value)
    existing.next_fire_at = first
    existing.version += 1
    return existing
