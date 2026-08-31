"""The Reminder writes, shared by the proposal handler and the `/reminders` screens.

Timing is never authored here: a resolved :class:`Schedule` arrives and the first fire is
computed from it. Editing text and changing the schedule are separate operations, which is
what keeps a reworded Reminder firing at the moment it always did.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import REMINDER_CATCHUP_GRACE_MINUTES, WEEKDAY_NAMES
from ...enums import ScheduleKind
from ...foundation.clock import Clock
from ...foundation.errors import DomainError
from ...foundation.models import Workspace
from ...foundation.workspace import bump_workspace
from .model import Reminder
from .schedule import (
    Schedule,
    next_fire,
    on_wall_clock,
    roll_forward,
    schedule_columns,
    schedule_of,
)


async def create_reminder(
    session: AsyncSession, *, instruction: str, schedule: Schedule, tz: ZoneInfo
) -> Reminder:
    """Store a Reminder and compute its first fire. The schedule arrives already resolved."""
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder = Reminder(
        instruction=clean, next_fire_at=_first_fire(schedule, tz), **schedule_columns(schedule)
    )
    session.add(reminder)
    await session.flush()
    await bump_workspace(session)
    return reminder


async def update_reminder_text(
    session: AsyncSession, reminder_id: int, instruction: str
) -> Reminder:
    """Edit what a Reminder tells the advisor, and nothing about when it fires."""
    reminder = await _editable_reminder(session, reminder_id)
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder.instruction = clean
    reminder.version += 1
    await bump_workspace(session)
    return reminder


async def reschedule_reminder(
    session: AsyncSession, reminder_id: int, *, schedule: Schedule, tz: ZoneInfo
) -> Reminder:
    reminder = await _editable_reminder(session, reminder_id)
    first = _first_fire(schedule, tz)
    for column, value in schedule_columns(schedule).items():
        setattr(reminder, column, value)
    reminder.next_fire_at = first
    reminder.version += 1
    await bump_workspace(session)
    return reminder


async def delete_reminder(session: AsyncSession, reminder_id: int) -> None:
    """Remove a Reminder outright; there is no archive."""
    reminder = await _editable_reminder(session, reminder_id)
    await session.delete(reminder)
    await bump_workspace(session)


async def _editable_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    """A Reminder the owner and the advisor may touch — never one Safwa derived."""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise DomainError("Reminder does not exist")
    if reminder.system:
        raise DomainError("That Reminder belongs to Safwa; change it in Settings")
    return reminder


def _first_fire(schedule: Schedule, tz: ZoneInfo) -> datetime:
    first = next_fire(schedule, previous=None, now=datetime.now(UTC), tz=tz)
    if first is None:
        raise DomainError("That schedule has no future occurrence")
    return first


async def reconcile_reminders(session: AsyncSession, now: datetime | None = None) -> None:
    """Fix `next_fire_at` on every repeating Reminder after downtime.

    Two things go stale while the process is down. A wall-clock schedule stores a local
    time, so after a timezone change "08:30" is a different UTC instant and every stored
    fire time is wrong at once; those are rebuilt. A schedule that came due meanwhile is
    rolled forward, but only once it is past the catch-up grace — inside the grace the row
    stays overdue, because the first poll firing it is the catch-up.

    A one-shot is never moved: it always fires, however late.
    """
    now = now or datetime.now(UTC)
    workspace = await session.get(Workspace, 1)
    tz = ZoneInfo(workspace.timezone if workspace else "UTC")
    grace = timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES)
    for reminder in await session.scalars(select(Reminder)):
        schedule = schedule_of(reminder)
        if not schedule.repeating:
            continue  # a one-shot always fires, however late
        if not on_wall_clock(schedule, reminder.next_fire_at, tz):
            rebuilt = next_fire(schedule, previous=None, now=now, tz=tz)
            if rebuilt is not None:
                reminder.next_fire_at = rebuilt
        if now - reminder.next_fire_at > grace:
            reminder.next_fire_at = roll_forward(
                schedule, previous=reminder.next_fire_at, now=now, tz=tz
            )


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


async def create_sprint_reminder(
    session: AsyncSession,
    *,
    instruction: str,
    at_time: time,
    anchor_at: datetime,
    tz: ZoneInfo,
    sprint_id: int,
) -> Reminder:
    """One warning a Sprint sets for itself: fires once, at the clock the Sprint started at.

    No owner set it, so it is Safwa's: hidden from `/reminders` and from the model, and
    removed by the Sprint ending rather than by hand.
    """
    reminder = await create_reminder(
        session,
        instruction=instruction,
        schedule=Schedule(kind=ScheduleKind.ONCE, at_time=at_time, anchor_at=anchor_at),
        tz=tz,
    )
    reminder.sprint_id = sprint_id
    reminder.system = True
    return reminder


async def delete_sprint_reminders(session: AsyncSession, sprint_id: int) -> None:
    """A finished Sprint's own warnings have nothing left to announce."""
    await session.execute(delete(Reminder).where(Reminder.sprint_id == sprint_id))
