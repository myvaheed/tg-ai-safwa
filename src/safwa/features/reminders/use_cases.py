"""The Reminder writes, shared by the proposal handler and the `/reminders` screens.

Timing is never authored here: a resolved :class:`Schedule` arrives and the first fire is
computed from it. Editing text and changing the schedule are separate operations, which is
what keeps a reworded Reminder firing at the moment it always did.

Don't disturb is the Profile's, never a Reminder's: every firing of an interval Reminder of
the owner's is worked out with it as it is now. One Remind made keeps to its item's moments.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import Workspace, bump_workspace
from ..profile.api import dont_disturb
from .api import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    Schedule,
    next_fire,
    on_wall_clock,
    parse_windows,
    roll_forward,
    schedule_columns,
    schedule_of,
)
from .model import Reminder, ScheduleKind

# The change a hook may follow up on: the owner's Reminder was created, however it was saved.
REMINDER_CREATED = "reminder.created"


async def create_reminder(
    session: AsyncSession,
    *,
    instruction: str,
    schedule: Schedule,
    tz: ZoneInfo,
    actor: ActorType = ActorType.USER_UI,
    item_type: str | None = None,
    item_id: int | None = None,
) -> Reminder:
    """Store the owner's Reminder and compute its first fire. The schedule arrives already
    resolved; an item is named when Remind on its Schedule made it."""
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder = Reminder(
        instruction=clean,
        next_fire_at=_first_fire(await firing_schedule(session, schedule, item_type), tz),
        item_type=item_type,
        item_id=item_id,
        **schedule_columns(schedule),
    )
    session.add(reminder)
    await session.flush()
    await bump_workspace(session)
    await _record(session, reminder, CREATE, actor)
    record_change(session, REMINDER_CREATED, reminder.id)
    return reminder


async def update_reminder_text(
    session: AsyncSession, reminder_id: int, instruction: str, *, actor: ActorType = ActorType.USER_UI
) -> Reminder:
    """Edit what a Reminder tells the advisor, and nothing about when it fires."""
    reminder = await _existing_reminder(session, reminder_id)
    before = snapshot(reminder)
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder.instruction = clean
    reminder.version += 1
    await _record(session, reminder, UPDATE, actor, before)
    await bump_workspace(session)
    return reminder


async def reschedule_reminder(
    session: AsyncSession,
    reminder_id: int,
    *,
    schedule: Schedule,
    tz: ZoneInfo,
    actor: ActorType = ActorType.USER_UI,
) -> Reminder:
    reminder = await _existing_reminder(session, reminder_id)
    before = snapshot(reminder)
    first = _first_fire(await firing_schedule(session, schedule, reminder.item_type), tz)
    for column, value in schedule_columns(schedule).items():
        setattr(reminder, column, value)
    reminder.next_fire_at = first
    reminder.fired = 0  # a new schedule's total counts from 0
    reminder.version += 1
    await _record(session, reminder, "reschedule", actor, before)
    await bump_workspace(session)
    return reminder


async def move_reminder(session: AsyncSession, reminder_id: int, item_id: int) -> None:
    """Hand a Reminder that Remind made on to the next instance of its item's series."""
    reminder = await _existing_reminder(session, reminder_id)
    reminder.item_id = item_id


async def delete_reminder(session: AsyncSession, reminder_id: int, *, actor: ActorType = ActorType.USER_UI) -> None:
    """Remove a Reminder outright; there is no archive."""
    reminder = await _existing_reminder(session, reminder_id)
    await _record(session, reminder, DELETE, actor, snapshot(reminder))
    await session.delete(reminder)
    await bump_workspace(session)


async def _record(
    session: AsyncSession,
    reminder: Reminder,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(
        session, "reminder", reminder, reminder.instruction, operation, actor, before
    )


async def _existing_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise DomainError("Reminder does not exist")
    return reminder


async def quiet_hours(session: AsyncSession) -> tuple[tuple[time, time], ...]:
    return parse_windows(await dont_disturb(session))


async def firing_schedule(
    session: AsyncSession, schedule: Schedule, item_type: str | None
) -> Schedule:
    """The schedule a Reminder fires by: an interval of the owner's waits out Don't disturb."""
    if schedule.kind is not ScheduleKind.INTERVAL or item_type is not None:
        return schedule
    return replace(schedule, quiet_windows=await quiet_hours(session))


async def follow_dont_disturb(session: AsyncSession, now: datetime | None = None) -> None:
    """Move every interval Reminder of the owner's to its first moment ahead that Don't
    disturb, just changed, leaves it: counted again from its start, so none fires twice."""
    now = now or datetime.now(UTC)
    tz = await _zone(session)
    windows = await quiet_hours(session)
    for reminder in await session.scalars(
        select(Reminder).where(
            Reminder.schedule_kind == ScheduleKind.INTERVAL.value,
            Reminder.item_type.is_(None),
            Reminder.next_fire_at.is_not(None),
        )
    ):
        schedule = replace(schedule_of(reminder), quiet_windows=windows)
        moment = next_fire(schedule, previous=None, now=now, tz=tz)
        if moment is not None and moment <= now:
            moment = roll_forward(schedule, previous=moment, now=now, tz=tz)
        if moment is None:
            await session.delete(reminder)
        else:
            reminder.next_fire_at = moment


async def _zone(session: AsyncSession) -> ZoneInfo:
    workspace = await session.get(Workspace, 1)
    return ZoneInfo(workspace.timezone if workspace else "UTC")


def _first_fire(schedule: Schedule, tz: ZoneInfo) -> datetime | None:
    if schedule.kind is ScheduleKind.CONDITION:
        return None  # an answer fires it, never the clock
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
    stays overdue, because the first tick firing it is the catch-up.

    A one-shot is never moved: it always fires, however late. A condition has no clock to
    move. A repeat that moves past its last day is deleted.
    """
    now = now or datetime.now(UTC)
    tz = await _zone(session)
    grace = timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES)
    for reminder in await session.scalars(select(Reminder).where(Reminder.next_fire_at.is_not(None))):
        schedule = await firing_schedule(session, schedule_of(reminder), reminder.item_type)
        if not schedule.repeating:
            continue  # a one-shot always fires, however late
        moment = reminder.next_fire_at
        if not on_wall_clock(schedule, moment, tz):
            moment = next_fire(schedule, previous=None, now=now, tz=tz)
        if moment is not None and now - moment > grace:
            moment = roll_forward(schedule, previous=moment, now=now, tz=tz)
        if moment is None:
            await session.delete(reminder)
        else:
            reminder.next_fire_at = moment
