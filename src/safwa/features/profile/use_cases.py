"""Profile-owned reconciliation of the Settings-backed Diary Reminder."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import WEEKDAY_NAMES
from ...enums import ScheduleKind
from ...foundation.errors import DomainError
from ...foundation.models import Workspace
from ...models import Reminder
from ...reminders import Schedule, next_fire, schedule_columns
from .model import UserProfile

DIARY_REMINDER_INSTRUCTION = (
    "End of day. Call the diary subagent for today, then propose what it reports."
)


async def sync_diary_reminder(
    session: AsyncSession, *, profile: UserProfile | None = None
) -> Reminder | None:
    """Reconcile only the internal Diary trigger derived from Profile Settings."""
    current = profile or await session.get(UserProfile, 1)
    if current is None:
        raise DomainError("User profile is not initialized")
    existing = await session.scalar(
        select(Reminder).where(
            Reminder.system.is_(True),
            Reminder.sprint_id.is_(None),
        )
    )
    if current.diary_time is None:
        if existing is not None:
            await session.delete(existing)
        return None

    workspace = await session.get(Workspace, 1)
    tz = ZoneInfo(workspace.timezone if workspace else "UTC")
    schedule = Schedule(
        kind=ScheduleKind.DAILY,
        weekdays=WEEKDAY_NAMES,
        at_time=current.diary_time,
    )
    extra = current.diary_instructions.strip()
    instruction = (
        f"{DIARY_REMINDER_INSTRUCTION} {extra}" if extra else DIARY_REMINDER_INSTRUCTION
    )
    first = next_fire(schedule, previous=None, now=datetime.now(UTC), tz=tz)
    if first is None:
        raise DomainError("That schedule has no future occurrence")

    if existing is None:
        existing = Reminder(
            instruction=instruction,
            system=True,
            sprint_id=None,
            next_fire_at=first,
            **schedule_columns(schedule),
        )
        session.add(existing)
        await session.flush()
        return existing
    if existing.instruction == instruction and existing.at_time == current.diary_time:
        return existing
    existing.instruction = instruction
    if existing.at_time != current.diary_time:
        for column, value in schedule_columns(schedule).items():
            setattr(existing, column, value)
        existing.next_fire_at = first
    existing.version += 1
    return existing
